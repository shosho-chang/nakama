# Nakama Workflow Inventory

> **凍結日**：2026-04-26
> **產出於**：Sub-agent B（Explore agent）over 2026-04-26 session
> **目的**：建立 nakama repo 的 agent × skill × workflow catalog，作為跨 task 上下文 baseline。從 Karpathy gist 哲學切入，wiki 不只 research 也是 personal management — 這份 inventory 把修修「醒著的所有事情」mapped 到對應的 agent / skill。

---

## 概述

Nakama 是為修修（Health & Wellness 內容創作者）設計的多 Agent AI 系統，部署於 VPS（202.182.107.202）與本機開發環境。系統透過 Syncthing 與 Windows/Mac 開發機同步 Obsidian LifeOS Vault，彼此協作完成內容創作、知識管理、系統維護等工作。

---

## View A — By Agent（9 個）

### Agent 1: Robin（考古學家）— 知識管理

- **角色定位**：掃描 Inbox 攝取外部內容（paper/article/book/podcast），產出 Source Summary、Concept & Entity 頁面，維護 KB/Wiki
- **部署**：VPS + 本機（本機優先 GPU mode，VPS fallback）
- **觸發來源**：
  - Cron `0 2 * * *` — 每天台北 02:00 執行 `python -m agents.robin`（Inbox KB ingest）
  - Cron `30 5 * * *` — 每天台北 05:30 執行 `python -m agents.robin --mode pubmed_digest`（PubMed daily digest）
  - Manual — Web UI `/kb/ingest`（`thousand_sunny/routers/robin.py`，16 routes）
  - HTTP API `/kb/research` — Robin KB search endpoint（已 skill 化 kb-search）
- **Pipeline**（高層）：
  1. Scan `Inbox/kb/` 檔案 → 檢查 `state.db` `files_processed` 已處理？
  2. 副檔名 or Web UI 推測 source type → 複製至 `KB/Raw/[type]/`
  3. Web UI Reader（optional）標記重點 & 註記 → Claude summary → `KB/Wiki/Sources/[title].md`
  4. 使用者輸入引導 + LLM 提取 Concepts & Entities → 建立/更新 `KB/Wiki/Concepts/` 與 `KB/Wiki/Entities/`
  5. 自動更新 `KB/index.md` + append `KB/log.md`
  6. 標記 `state.db` 檔案已處理
  7. PubMed digest：RSS feed → filter + rank → daily digest page + 精選論文頁
- **核心 tools / skills 使用**：`shared/obsidian_writer.py`、`agents/robin/categories.py`、`agents/robin/chunker.py`、`agents/robin/ingest.py`、`agents/robin/kb_search.py`、`agents/robin/pubmed_*.py`、`shared/gemma_local.py`（Qwen 3.6 GPU LLM）
- **輸出**：`KB/Raw/[type]/`、`KB/Wiki/Sources/`、`KB/Wiki/Concepts/`、`KB/Wiki/Entities/`、`KB/log.md`、`state.db`
- **依賴的其他 agent**：無
- **狀態**：Production
- **已知 backlog / 待修**：
  - PR #141 OPEN — Reader UI paste-to-vault 剪貼簿圖片 + metadata pills + DOI/PMID 連結
  - PubMed OA 全文品質觀察（pymupdf4llm 多欄學術版式可能不足 → Docling/BabelDOC Phase 2）
  - Robin /kb/research 內 top_k=8 enhancement 待修
  - **重大設計缺陷**：`_update_wiki_page` 不是 aggregator（見 `docs/plans/2026-04-26-ingest-v2-redesign-plan.md`）
- **設計對齊度**：高（流程清晰，schema 完整），但 update path 需 v2 重寫。

---

### Agent 2: Nami（航海士）— 日常秘書 / Morning Brief

- **角色定位**：整合各 agent 前夜產出 → 產出 Morning Brief + 數據週報 + 行事曆同步 + Gmail triage 與草稿
- **部署**：VPS（Slack bot daemon）+ 本機（開發）
- **觸發來源**：
  - Cron `0 7 * * *` — 每天台北 07:00（**未實現**，待開發）
  - Slack mention `@nami` — Socket Mode handler（`gateway/handlers/nami.py`），11 個 tools（Calendar 4 + Gmail 6 + brainstorm 1）
- **Pipeline**：
  1. **Morning Brief** — Franky probe + Zoro 推薦題 + Robin digest → Slack DM（**skeleton 存在 code，未實現觸發**）
  2. **Calendar + Task sync** — Google Calendar event → LifeOS Task frontmatter（PR #40 merged 已 VPS deployed）
  3. **Gmail triage** — Primary 未讀 + 超時待回 → 列表 + 批量回覆建議 + 草稿
  4. **Deep Research** — Slack query → 檔案搜 + 網路查 + 報告 md（PR #59-64 merged）
  5. **Brainstorm synthesizer** — Zoro + 第二 agent 並行跑後聚合（PR #51 merged）
- **核心 tools / skills 使用**：`shared/google_calendar.py`、`shared/google_gmail.py`、`shared/robin_kb_search.py`、`gateway/handlers/nami.py`、`shared/deep_research.py`
- **輸出**：`AgentBriefs/Nami/YYYY-MM-DD.md`、`KB/[research-topic].md`、`Tasks/[task-id].md`、Slack DM、`state.db`
- **依賴**：Robin（KB 查詢）、Zoro（brainstorm 題目）、Franky（系統狀態）
- **狀態**：部分上線
- **已知 backlog**：Morning Brief cron 未實現、Slack thread 續問實機未驗、大量郵件搜尋策略優化、Nami agent loop deep research max_tokens 預算
- **設計對齊度**：中-高。Calendar / Gmail tools 完整，缺 Morning Brief 實現 + Zoro 時序搭配。

---

### Agent 3: Zoro（劍士）— 情報蒐集 / Brainstorm Scout

- **角色定位**：從 Google Trends/Reddit/YouTube/PubMed 蒐集熱點訊號 → 推薦每日選題到 #brainstorm
- **部署**：VPS
- **觸發**：Cron `0 5 * * *` — 每天台北 05:00（`python -m agents.zoro scout`）
- **Pipeline**：
  1. `gather_signals()` — Trends / Reddit / YouTube / PubMed 收原始訊號
  2. `velocity_gate()` — 熱度過濾
  3. `signals_to_topics()` — 多訊號聚合
  4. `relevance_gate()` — keyword 預濾 + LLM health relevance
  5. `novelty_gate()` — agent_memory 14 天內已推過 skip
  6. `cooldown_gate()` — 48h 近似題 skip
  7. `pick_best_topic()` → `publish_to_slack()` — Zoro bot post #brainstorm
- **核心 tools / skills 使用**：`agents/zoro/keyword_research.py`（已 skill 化）、`agents/zoro/brainstorm_scout.py`、`shared/pushed_topics.py`、`shared/llm.ask()`
- **輸出**：Slack #brainstorm post、`state.db`、cron stdout JSON
- **依賴**：無（獨立 signal gather）
- **狀態**：Production（Slice C1 deployed）
- **已知 backlog**：Slice D Reddit OAuth、Item 4 reddit_zh、Item 5 twitter_zh（PR #144 merged 後其他 6 GH issues backlog）
- **設計對齊度**：高。Reddit/YouTube 受 VPS IP 限制，Slice D 待 OAuth。

---

### Agent 4: Sanji（廚師）— 社群營運 / 監控

- **角色定位**：Fluent Community 社群營運、監控未回覆貼文、活動策劃、成員互動
- **部署**：VPS（Slack bot）
- **觸發**：Slack mention `@sanji`
- **Pipeline**：
  1. **第一版**：Slack bot persona（Grok xAI model）+ smoke test（PR #31，2026-04-20 上線）
  2. **後續**（待設計）：mention → 社群互動語義路由、回覆監控、活動提醒
  3. **FluentCommunity 整合**（Phase 2）：webhook + Fluent CRM 會員資料
- **核心 tools**：`shared/llm.ask()`（xAI Grok）、`shared/prompt_loader.py`、`gateway/handlers/sanji.py`
- **輸出**：Slack reply
- **狀態**：開發中（只有 persona 殼）
- **設計對齊度**：低。三個未決：(1) 社群平台是什麼（Circle? Discord? Fluent 內建?）、(2) 監控訊號（未回覆貼文 / 新成員 / engagement）、(3) Fluent Community 與 Fluent CRM 整合邊界。

---

### Agent 5: Usopp（狙擊手）— 發布管線

- **角色定位**：把 approval_queue 的核准 draft 原子性發布至 WordPress、YouTube、社群媒體；電子報管理（Fluent CRM）
- **部署**：VPS（systemd daemon `nakama-usopp.service`）+ Phase 1 WordPress only
- **觸發**：Daemon loop（continuous）— 每 30 秒 poll approval_queue claimed 狀態
- **Pipeline**：
  1. `claim_approved_drafts(worker_id, source_agent='brook', batch=5)` — atomic SELECT...FOR UPDATE
  2. Build `PublishRequestV1`
  3. `Publisher.publish()` — per-step state machine（idempotent via nakama_draft_id）
  4. LiteSpeed cache purge（WP hook auto-invalidate）
  5. Mark approval_queue row `published` / `failed`
  6. Franky `reset_stale_claims()` 5 分鐘 cron reset failing rows
- **核心 tools**：`agents/usopp/publisher.py`、`shared/wordpress_client.py`、`shared/schemas/approval.py`、`shared/schemas/publishing.py`、`shared/litespeed_purge.py`
- **輸出**：WordPress post、`state.db`（approval_queue + publish_jobs）
- **依賴**：Brook（queue source）、Franky（stale reset）
- **狀態**：Production
- **已知 backlog**：Phase 1.5 YouTube publish、Phase 2 Fluent CRM 電子報 + 社群媒體 cross-post、`update_post` action_type Phase 1 fail-closed
- **設計對齊度**：高。遵守 ADR-005b reliability 原則。

---

### Agent 6: Brook（音樂家）— Composer

- **角色定位**：素材 → Claude 對話式撰寫 → Gutenberg HTML + metadata → enqueue approval_queue
- **部署**：VPS（Web UI `/compose`）+ 本機（FastAPI routers）
- **觸發**：Web UI `/compose`、Manual `compose_and_enqueue(topic)`、`article-compose` skill
- **Pipeline**：
  1. Topic → outline 產生
  2. Per-section drafting iterative loop
  3. Style profile detection（`config/style-profiles/*.yaml`）
  4. Compliance scan（敏感詞、醫療免責、品牌）
  5. Gutenberg HTML generation（DraftV1 → BlockNodeV1）
  6. SEO enrich (Phase 1.5) — SEOContextV1 整合
  7. Export draft → enqueue approval_queue
- **核心 tools**：`agents/brook/compose.py`、`agents/brook/style_profile_loader.py`、`agents/brook/compliance_scan.py`、`shared/gutenberg_builder.py`、`thousand_sunny/routers/brook.py`、`shared/seo_narrow.py`
- **輸出**：approval_queue (status='pending')、brook_conversations、brook_messages
- **依賴**：Usopp（claim 與發布）、Robin（style profile 學習）
- **狀態**：Production（Phase 1 完成；SEO Slice C 整合 ongoing）
- **已知 backlog**：Phase 1.5 SEO context 整合（PR #139 OPEN）、Phase 2 圖片生成 + multi-platform repurpose
- **設計對齊度**：高。

---

### Agent 7: Franky（船匠）— 系統維護

- **角色定位**：VPS 健康監控 → alert routing → Slack DM；備份驗證；週報
- **部署**：VPS（cron + systemd probe endpoint）
- **觸發**：
  - Cron `*/5 * * * *` — Health check
  - Cron `30 3 * * *` — R2 backup verify
  - Cron `0 10 * * 1` — Weekly digest
  - External GHA probe（`.github/workflows/external-probe.yml`）
- **Pipeline**：
  1. Health probes 5 targets
  2. Alert dedup + Slack dispatch
  3. Backup verify（R2 freshness ≤ 1d）
  4. Weekly digest（state.db metrics + token cost）
- **核心 tools**：`agents/franky/health_check.py`、`agents/franky/alert_router.py`、`agents/franky/slack_bot.py`、`agents/franky/r2_backup_verify.py`、`agents/franky/weekly_digest.py`、`shared/schemas/franky.py`
- **輸出**：Slack DM、Bridge `/bridge/franky` dashboard、`state.db`
- **依賴**：無
- **狀態**：Production（Phase 1 全 in；PR #126/#127 merged）
- **設計對齊度**：高。

---

### Agent 8: Chopper（醫生）— 社群健康顧問

- **角色定位**：社群會員 1:1 健康問答，調用 KB + 記憶會員背景
- **部署**：待開發
- **觸發**：webhook（社群平台 TBD）
- **Pipeline**（草圖）：
  1. 會員 query → 成員記憶查詢
  2. KB 查詢（kb-search skill）
  3. Claude 文獻支撐回答 + 免責 + citation
  4. 更新會員記憶
- **核心 tools**：`shared/member_profile.py`（待開發）、kb-search skill、HITL 三階段 approval
- **狀態**：規劃中
- **設計對齊度**：低。三個未決：平台選擇、會員記憶架構、HITL 敏感詞清單。

---

### Agent 9: Sunny（甲板）— Bridge Dashboard

- **角色定位**：Thousand Sunny 首頁 → multi-agent 控制台
- **部署**：VPS（Web UI）
- **觸發**：瀏覽 `/bridge`
- **Pipeline**：列出 9 agent 狀態 / drafts queue / memory CRUD / cost dashboard / agent log
- **核心 tools**：`thousand_sunny/routers/bridge.py`、`shared/agent_memory.py`、`shared/approval_queue.py`、`shared/pricing.py`
- **輸出**：Web UI 儀表板
- **狀態**：Phase 1 完成（PR #41/42/44/45 + Phase 4 + #136-137/#140 merged）
- **設計對齊度**：中-高。

---

## View B — By Use Case（12 個）

### U1. 內容創作（部落格 / 文章）

- **狀態**：部分支援
- **鏈**：keyword-research（Zoro）→ seo-keyword-enrich（Phase 1.5）→ Brook compose → Usopp publish；Robin KB query 撰寫時參考
- **入口**：`/keyword-research` skill → `/seo-keyword-enrich` → `/compose` Web UI / `article-compose` skill → Bridge `/bridge/drafts` 核准
- **缺口**：SEO enrich 整合 Phase 1.5（PR #139 OPEN）、關鍵字調整循環無自動 feedback、Featured image 人工選、`seo-audit-post` skill 待實作、multi-platform repurpose 未實現

### U2. Podcast 製作

- **狀態**：部分支援（轉錄有，發布沒）
- **鏈**：transcribe skill → SRT + QC report
- **入口**：Slack Claude Code `/transcribe`
- **缺口**：Show notes 生成（從 transcript + KB query）、發布流程（YouTube 字幕、Podcast RSS、Fluent CRM 通知）、長音檔 ASR 品質優化

### U3. 書籍 / 論文閱讀心得

- **狀態**：部分支援（KB ingest 有，跨書整合部分）
- **鏈**：Robin KB ingest（auto cron 02:00）+ textbook-ingest skill（手動，Phase 1 MVP）+ PubMed digest（cron 05:30）
- **入口**：放檔到 `Inbox/kb/`、`/textbook-ingest <path>`
- **缺口**：Reader 翻譯設計分歧、跨書 Concept 聚合邏輯未寫、textbook-ingest Phase 1.5 待 v2 redesign（見 ingest plan）、Chrome Extension 一鍵剪缺 plugin shell

### U4. 社群互動 / Q&A

- **狀態**：缺料（架構設計，無實現）
- **鏈**：Chopper agent + Robin KB search + member profile + Fluent Community webhook
- **缺口**：平台未選定、會員記憶架構未決、HITL 三階段敏感詞清單與免責措辭

### U5. 日程 / 任務管理

- **狀態**：部分支援（Calendar sync done，Task sync 待驗證）
- **鏈**：Nami Google Calendar 4 tools、`project-bootstrap` skill、Calendar → Task sync（PR #40）、Morning Brief（skeleton）
- **入口**：Slack `@nami list events`、`/project-bootstrap`、cron 同步
- **缺口**：sync 反向（Task done → Calendar）、Morning Brief cron 未實現、Slack thread 續問未驗、Gmail triage 與 task 關聯

### U6. 信件處理

- **狀態**：部分支援（triage + draft 有，自動回覆沒）
- **鏈**：Nami Gmail 6 tools、Triage rules、Robin KB query（sales kit）
- **入口**：Slack `@nami list unread / search / create_draft / send`
- **缺口**：自動回覆路由 + HITL approval、Sales kit 缺料、Thread 續問邏輯、Bridge UI 草稿 preview

### U7. KB 維護

- **狀態**：缺料（版控有，去重/衝突沒自動化）
- **鏈**：Robin ingest + 定期 audit（未實現）+ Vault git
- **缺口**：去重機制（multi-source dedup）、衝突偵測（同概念異名 merge）、Link rot 檢測、Schema migration 自動化、定期 audit 缺失（→ ingest v2 redesign 中央處理）

### U8. 訂閱期刊與最新文獻 monitoring

- **狀態**：部分支援（PubMed digest done，Zotero 規劃中）
- **鏈**：Robin PubMed digest（cron 05:30）+ Zoro brainstorm scout（cron 05:00）+ Zotero（規劃中）
- **缺口**：Zotero 整合無 timeline、PubMed 關鍵字配置只有 yaml 無 UI、多語言 RSS（Reddit_zh / Twitter_zh 待 OAuth）、Priority 排序

### U9. SEO 與部落格體檢

- **狀態**：部分支援（keyword research + enrich Phase 1 done；audit + optimize Phase 1.5-2）
- **鏈**：keyword-research → seo-keyword-enrich → seo-audit-post（待實作）→ seo-optimize-draft（Phase 2）→ Brook compose → Usopp publish + SEOPress meta + Franky digest
- **入口**：skill commands、Brook integrate（PR #139 OPEN）、Bridge dashboard（Phase 2）
- **缺口**：seo-keyword-enrich 整合 Phase 1.5 剛 merge 待實測、seo-audit-post 設計凍結未開工、seo-optimize-draft Phase 2、Health vertical DataForSEO 限制、Cannibalization heuristic、舊文章重新發現邏輯未實現

### U10. 內容重製（部落格 → IG / YT / Newsletter）

- **狀態**：缺料
- **鏈**：Brook composer 可選 repurpose module（Phase 2）+ Usopp YouTube/IG API（Phase 2-3）+ 新 skill `repurpose-blog-to-short-form`（待設計）
- **缺口**：Platform adapter（IG / YT short / Newsletter 格式差異）、Visual asset、Hashtag/Caption 邏輯、Publishing logic（單 draft clone vs 獨立 DraftV1）、時序規劃

### U11. 系統運維 / monitoring

- **狀態**：完整可跑（Franky Phase 1 全 in）
- **鏈**：Franky health probes + alert + R2 verify + weekly digest + Bridge `/bridge/franky`
- **入口**：cron 自動 + Bridge dashboard
- **缺口**：Metric 範圍（disk/memory/CPU/log error rate）、SLA tracking、Cost forecast、Capacity planning

### U12. 視覺資產生成

- **狀態**：缺料
- **鏈**：Brook image generation module（Phase 2）+ 新 skill or agent
- **缺口**：免費資源（Unsplash/Pexels）、Brand consistency、Legal、Thumbnails sizing、Cost vs quality tradeoff（Flux $0.02 vs DALL-E $0.04 vs Midjourney $10/mo）

---

## 基礎設施

### 核心決策（ADR）

| ADR | 內容 | 狀態 |
|-----|------|------|
| ADR-001 | Agent 職責分配 | Accepted |
| ADR-002 | 記憶系統 3 層架構 | Phase 1-3 done, Phase 4 等 MemPalace |
| ADR-003 | Telegram gateway | Proposed |
| ADR-004 | Slack gateway multi-bot | Implemented |
| ADR-005a | Brook Gutenberg pipeline | Phase 1 merged |
| ADR-005b | Usopp WP publishing | Phase 1 merged |
| ADR-005c | Bricks template 人工維護 | Accepted |
| ADR-006 | HITL approval queue | Phase 1 + 2 merged |
| ADR-007 | Franky scope expansion | Merged |
| ADR-008 | SEO observability | Proposed |
| ADR-009 | SEO solution architecture | Phase 1.5 Slice A merged |
| ADR-010 | Textbook ingest | Merged，**Phase 1.5 待 v2 redesign（2026-04-26）** |

### Cron Schedule（VPS Asia/Taipei）

| 時間 | Agent | 任務 |
|------|-------|------|
| 02:00 | Robin | Inbox KB ingest |
| 03:30 | Franky | R2 backup verify |
| 04:00 | system | state.db → R2 backup |
| 05:00 | Zoro | Brainstorm scout |
| 05:30 | Robin | PubMed digest |
| 07:00 | Nami | Morning Brief（**未實現**）|
| 10:00 週一 | Franky | Weekly digest |
| 每 5 分鐘 | Franky | Health probes |

### Systemd Services（VPS）

| Service | 說明 |
|---------|------|
| `thousand-sunny` | Uvicorn Web UI port 8000 |
| `nakama-usopp` | Publisher daemon poll approval_queue |
| `nakama-gateway` | Slack gateway daemon Socket Mode |

**重啟分開**：thousand-sunny 與 nakama-gateway 是獨立 service。

### Deployed Skills

| Skill | 狀態 |
|-------|------|
| keyword-research | Production |
| kb-search | Merged PR #142 |
| textbook-ingest | Phase 1 MVP merged，**v2 redesign 中（2026-04-26）** |
| seo-keyword-enrich | Phase 1.5 整合中 |
| article-compose | Production |
| project-bootstrap | Production |
| transcribe | Production |
| firecrawl / prior-art-research / code-review / skill-creator | Plugins installed |

### VPS 部署架構

```
Obsidian Vault（本機）↕ Syncthing ↕ VPS /home/nakama/LifeOS/
Web Server（Thousand Sunny）→ port 8000 → Cloudflare Tunnel（nakama.shosho.tw）
Cron Jobs → 各 agent → state.db + vault 寫入
Gateway（Slack daemon）→ Socket Mode → 9 Slack Bot Apps
```

---

## 整體對齊評估

### Schema drift

- **高對齊**：Robin、Zoro、Usopp、Brook、Franky（流程清晰、schema 完整）
- **中對齊**：Nami（Morning Brief TODO）、Brook（SEO 整合進行中）
- **低對齊**：Sanji（只有 persona）、Chopper（規劃中）

### Pipeline gap（需手動橋接）

1. 內容創作全流程缺 SEO enrich end-to-end 實測
2. Podcast show notes 缺
3. 社群 Q&A 平台未定
4. 任務管理 bidirectional sync 缺
5. 內容 repurpose 完全缺
6. 視覺資產設計 + 實作缺

### 角色重疊風險

- **低**：9 agent 職責邊界清晰（ADR-001）
- **中**：Nami + Chopper 都涉及會員互動（差異 — Nami 秘書、Chopper 1:1 健康問答）

### SPOF（Single Point of Failure）

| 風險 | 影響 | 緩解 |
|------|------|------|
| VPS down | cron + daemon 全停 | R2 backup 可恢復；Syncthing 保護 vault |
| Slack token 過期 | Gateway bot 死 | systemd restart |
| approval_queue 卡 | Usopp publish 停 | Franky reset_stale_claims 5min cron |
| Robin GPU（本機） | PubMed digest 改 VPS local LLM | `DISABLE_ROBIN=1` fallback |

---

## 下一步準備清單

### 立即（今週）

- [ ] **textbook-ingest v2 redesign**（→ `docs/plans/2026-04-26-ingest-v2-redesign-plan.md`）
- [ ] Nami Morning Brief cron 實現 + 實測
- [ ] PR #141（Robin Reader UI）merge + smoke test
- [ ] PR #139（Brook SEO integrate）merge + 全流程測試

### 短期（2-4 週）

- [ ] seo-audit-post skill（Phase 1.5）設計與實現
- [ ] Podcast show notes 生成（skill or agent）
- [ ] Chopper 核心決策（平台、記憶、敏感詞）→ skeleton
- [ ] Sanji 監控流程設計

### 中期（1-3 月）

- [ ] Phase 2：YouTube publish（Usopp）
- [ ] Phase 2：Featured image 生成（Unsplash + Flux）
- [ ] Phase 2：內容 repurpose（Brook skill family）
- [ ] ADR-008 SEO observability

### 長期（3+ 月）

- [ ] Phase 3：BabelDOC 雙語 PDF（Robin）
- [ ] Phase 3：Zotero 整合
- [ ] Phase 3：Chopper MemPalace
- [ ] Phase 4：Bridge 美學迭代
