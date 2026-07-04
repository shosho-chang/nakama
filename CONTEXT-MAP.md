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
- **Thousand Sunny** (`thousand_sunny/`) — Web presentation 平台 / chassis：所有 web UI、Bridge dashboard、各 agent router；HMAC cookie + API key auth；**Sunny 是船本身（platform），不是 agent crew member**（ADR-029 §2 凍結）；glossary [thousand_sunny/CONTEXT.md](thousand_sunny/CONTEXT.md)；參見 [reference_bridge_ui_mutation_pattern](memory/claude/reference_bridge_ui_mutation_pattern.md)
- **video** (`video/`) — Video Production Line 的 Node.js + Hyperframes + TypeScript subproject；Hyperframes HTML compositions（bigstat / thumbnail 等）+ B-roll per-beat renderer；process boundary 跟 Python 主 repo 切開；Brook video line orchestrator（`agents/brook/script_video/`，原 `agents/foundry/`，ADR-050 已遷入）透過 Node.js CLI 呼叫；`src/parser/` markdown DSL parser 已退役（storyboard.yaml 取代，ADR-050 D3）；參見 ADR-032 + ADR-050

## Relationships

- 每個 Agent context 透過 `shared/anthropic_client.ask_claude()` 呼叫 LLM；token cost 記入 `state.db`
- 每個 Agent context 透過 `shared/events.py` 互發事件（如 Robin → Nami briefing 注入）
- 每個**已落地** Agent context 暴露 web 表面透過 `thousand_sunny/routers/<agent>.py`（Robin / Zoro / Brook / Franky 已落地；Nami / Sanji / Usopp / Chopper 未落地，dashboard 顯示為 disabled card）
- **Brook ← Zoro** (`SEOContextV1`)：Zoro 跑 keyword-research + seo-keyword-enrich 產出 SEO context block，Brook compose 時 consume
- **Robin ← Brook** (KB lookup)：Brook compose 時可呼叫 KB search 拉素材
- **Usopp ← Brook + Sanji**：Brook 產長文輸出、Sanji 產社群素材，Usopp 排程發布；含 ADR-006 HITL approval gate
- **Brook video line ↔ video subproject**（ADR-032/ADR-050）：pipeline 吃 `/transcribe` 產的 SRT → chinese_normalizer + LLM planner 產 `storyboard.yaml`（exact-copy anchor + 兩層 HITL approve）→ render_dispatcher 呼叫 `video/compositions/` Hyperframes render per-beat B-roll mp4（headless Chrome）→ Python 端 emit FCPXML 1.10（V1 talking head + lane-1 B-roll）；DaVinci import 後修修微調 → YT；選配前置：拍掌 marker `cleanup` stage（ADR-050 D3）
- **Brook video line ← Robin**（Phase 1.5，read-only）：`refs.yaml` 的 `book_slug_robin` 對 Robin Reader URL scheme，書內引用 B-roll 走 reader-playwright 錄真實書頁（ADR-032 Phase 1.5 接通）

## Per-context glossary

各 context 內部詞彙待 lazy creation —
開始 grill 該 context 時才寫 `agents/<name>/CONTEXT.md` 或 `thousand_sunny/CONTEXT.md`。

目前已凍結的 cross-context 名詞（避免歧義）：

- **「SEO solution」** = 三個用途集合：keyword research（**Zoro**，向外探索新主題）+ audit（**Brook**，對既有文章打分 + 改稿建議）+ enrich（**Brook**，為寫稿備 SEO context）。哲學分界「向外 = Zoro / 對內 = Brook」於 2026-04-29 grilling 凍結（落實 ADR-001 line 38 預留的 Brook 擴展選項）；參見 ADR-008 + ADR-009；不是新 agent
- **「approval queue」** = ADR-006 定義的 Usopp publish 前 HITL 站；不是 to-issues 的 `HITL` label
- **「EntityReviewItem」** = ADR-024 promotion gate 的第三種 ReviewItem subtype（前兩種：`SourcePageReviewItem` / `ConceptReviewItem`），由 ADR-034 凍結。**單一 class + `entity_type` enum** 覆蓋 Person / Organization / Book / Place — 不拆 sub-class（呼應 `ConceptReviewItem` 不拆 sub-domain 的既有 pattern）。entity-specific 欄位走 `entity_metadata: dict[str, Any]` 不污染 schema 階層
- **「Hybrid Entity gate」** = ADR-034 凍結的 Entity promotion 分流：Book Entity 走 `kb_writer.write_book_entity()` auto-create（修修主動 ingest = approved by definition），Person / Org Entity 走 promotion gate + confidence fast-track。雙路徑並存有意設計，不是 transitional state
- **「confidence fast-track」** = ADR-034 凍結的 ReviewItem 自動審批機制：`canonical_match.confidence > 0.9` auto-approve（不進 UI review queue 但仍記入 manifest），`0.5-0.9` 進 queue，`< 0.5` 進 queue 預設 defer。LLM 變強時調高 threshold 即可吸收，不需 redesign gate
- **「surface」** = Thousand Sunny 的個別 web 頁面 / 路由（如 `/bridge/franky`、`/zoro/keyword-research`）；不是 GTM / 行銷 surface
- **「SEO 中控台」** = `/bridge/seo` surface 的別名，SEO solution 操作 hub。**跨三 agent**：Zoro（keyword research）+ Brook（audit / enrich）+ Franky（ranking telemetry，ADR-008）。v1 三 section：(1) WP 文章列表 + lazy audit 分數、(2) 攻擊中目標關鍵字（讀 `config/target-keywords.yaml`）、(3) 排名變化（v1.1 等 ADR-008 Phase 2a-min 落地接 `gsc_rows` db）；2026-04-29 grilling 凍結，ADR-029 v2 補正 Franky owner
- **「audit review session」** = SEO 中控台底下「點進文章 → 跑新 audit → Y+ 左右對照」的單次審稿動作；以 `audit_results.suggestions_json` 落 db 持久化（resumable，無另開 session 表）；review 完成後一鍵 export 進 ADR-006 `approval_queue` 走既有 publish HITL — **不直接寫 WP**
- **「slice」** = vertical slice = 跨層（schema / API / UI / tests）的薄完整路徑；對應現有 Slice A/B/C 慣例
- **「chassis-nav」** = bridge surface 頂層 nav bar（`templates/bridge/_chassis_nav.html` partial 是 single source of truth）。**ADR-029 v2 凍結 dual-axis 原則**（取代 2026-04-29 的「agent-rooted 頂層直到擠爆才 dropdown」原則）：nav 依 task frequency × semantic similarity 組織 — 高頻 cross-agent workflow 拿 top-level slot（DRAFTS、SEO）；agent 收進單一 Fleet ▾ dropdown（dashboard grid 為主要視覺入口）；低頻 cross-cutting ops 收進 Ops ▾ dropdown（COST / LOGS / MEMORY / DOCS）。原則 component-agnostic（橫向 dropdown 目前；左 sidebar 為視覺探索階段已知候選）。`aria-current` 嚴格對齊 URL，不表 user journey trail
- **「dual-axis nav」** = ADR-029 v2 凍結的 Bridge IA 心智模型。**Agent axis**（Fleet ▾ + dashboard grid）：每個 agent 有單一 canonical home（console）；single-agent function 收進該 agent console（如 HEALTH → Franky、REPURPOSE → Brook）。**Workflow axis**（top-level slots）：跨 agent 的 work 拿獨立 top-level surface（DRAFTS、SEO）；分界線：「這 work 需不需要跨 agent 協調」。**Ops axis**（Ops ▾）：cross-cutting 觀測 surface 不屬於任何 agent。雙軸並存，不是 agent-first 也不是 task-first
- **「breadcrumb」** = page-header 之上一行的 user journey trail（如 `← /bridge/seo · 找新關鍵字 → ZORO · KEYWORD RESEARCH`），用來補位 chassis-nav 失去的「從哪來」資訊。Always 顯示（不 referrer-detect），用既有 `nk-caps` token
- **「script-driven video」/「腳本式影片」/「Video Production Line」** = ADR-015 開題、ADR-032 重寫技術、ADR-050 歸屬 Brook 的 cross-cutting context。修修最高價值 content workflow：照稿錄 A-roll →（選配）拍掌 marker cleanup → `/transcribe` SRT → LLM storyboard plan（兩層 HITL）→ Hyperframes B-roll render → 出 DaVinci FCPXML → 修修微調 ≤30 分鐘上 YT。**不是** Line 1/2/3（podcast/book/literature → 多 channel 文字）的延伸——input/output shape 不同，是 sibling 不是 extension
- **「Manifest」** = ~~ADR-015 workflow 的 single source of truth JSON schema~~ **Superseded（ADR-032/ADR-050）**：single source of truth 現為 `storyboard.yaml`（planner 輸出 + Bridge UI 就地編輯）；Manifest JSON 隨 markdown DSL parser 退役
- **「LLM Router」** = `shared/llm_router.py` 解析 `(agent, task) → model_id`、`shared/llm.py` facade 跨 provider dispatch；ADR-026 加 auth 維度後，router 同時解析 `(agent, task) → auth_policy`。**不是**新建，是 2026-04-20 起的 Q1 hybrid 方案延伸
- **「Auth policy」** = ADR-026 凍結的一次 LLM call 計費路徑語意，**三元值** `api` / `subscription_preferred` / `subscription_required`。`subscription_*` = 走 provider 訂閱 quota（Anthropic = Max Plan via `claude` CLI subprocess）；`api` = bare SDK + API key 計費。`_preferred` = 條件不滿足軟降 api + warn；`_required` = 條件不滿足 raise。預設 `subscription_preferred`（修修長期 Max Plan）
- **「Hard-lock override」** = `NAKAMA_REQUIRE_MAX_PLAN=1` env，process-wide 最高優先序，**映射為 `subscription_required`**（不是獨立語意層）；保留給 sandcastle / textbook ingest 必須 100% 走訂閱的場景
- **「Fallback reason」** = ADR-026 凍結的軟降 / raise enum，落 `api_calls.fallback_reason` column；值：`NO_OAUTH_TOKEN` / `PROVIDER_NOT_SUPPORTED` / `CLI_BINARY_NOT_FOUND` / `CLI_SUBPROCESS_ERROR` / `TOOL_USE_NOT_SUPPORTED_VIA_CLI`
- **「mistake removal」/「cleanup stage」** = 拍掌 marker 偵測（修修錄錯時拍兩下手做 marker）→ ripple-delete。ADR-015 原 stage 1；ADR-050 D3 refit 為單一 video pipeline 的**選配前置 `cleanup` stage**（輸出形狀 — ripple-delete FCPXML vs 直接剪乾淨 mp4 — 於 ADR-050 實施 PR-4 跟修修確認後定）
- **「B-roll segment」** = ADR-015 凍結、ADR-032 沿用的概念。Hyperframes **不 render 整支影片**，只 render 各 beat 為個別 mp4 clips（content-addressed `b_roll_<hash>.mp4`），FCPXML 把它們塞進 lane-1（V2）；A-roll 留 V1。架構反轉的核心
- **「Mode A / Mode B」** = ~~ADR-015 引用視覺化雙模式（PyMuPDF bbox DocumentQuote / QuoteCard）~~ **Superseded（ADR-032）**：書/網頁引用改走 reader-playwright / web-playwright 錄真實頁面 + highlight 動畫（保留「引用真的來自這本書」的視覺契約），非書頁類走 Hyperframes；見 `memory/claude/project_broll_dual_path_architecture.md`
- **「per-episode 目錄」** = `data/script_video/<episode-id>/` 自包含結構（ADR-032 §5 + ADR-050 D4）：episode.yaml（含 `stages:` provenance 欄位）+ raw_recording.mp4 + transcript.srt + refs.yaml（選配）+ storyboard.yaml + out/（content-addressed b_roll mp4 + episode.fcpxml）。~~跨集 embedding cache~~（BGE-M3 fuzzy match 路線已廢棄，ADR-032）

## ADR location

系統級決策位於 `docs/decisions/ADR-NNN-*.md`（**不是** mattpocock 預設的 `docs/adr/`）。
context 子目錄的 ADR 待第一個 context-specific decision 出現時 lazy 建立。

## Flagged ambiguities

開 grill 後發現的詞彙衝突在這裡累積。

- **「Line N」vs「script-driven video」** — Line 1/2/3 是 ADR-014 RepurposeEngine 的 source → 多 channel 文字 fan-out（Brook compose）。**script-driven video（ADR-015）不是 Line 4**——它是 single source + single output（mp4 via DaVinci），sequential pipeline 不 fan-out。grill 凍結為 sibling context，避免被誤稱「Line 4」
