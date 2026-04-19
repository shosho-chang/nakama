# PRD — Phase 4 Bridge UI（Memory 管理 + Cost Dashboard）

**Status**: Approved for implementation (2026-04-19 — Q4 confirmed, 修修「開始動工」go-ahead)
**Author**: Claude (Auto mode 自主草擬)
**Depends on**: Phase 1-3 Memory System（已部署 VPS，commit 62007b8）、`api_calls` cost tracking backend（已 wired）

---

## 1. 背景

Phase 1-3 把 Nami 的 user memory（修修的偏好/事實/決策）跑起來了：Haiku 4.5 自動抽取 → SQLite `user_memories` → 新對話注入 context block。VPS 實測通過。

目前兩個缺口需要 UI 填上：

1. **記憶透明度**：記憶自動抽取 + merge，但修修沒辦法看到抽了什麼、改錯的怎麼修、不想記的怎麼刪。已知小瑕疵（主詞前綴、type 誤判）目前只能靠 SQL。
2. **Cost 能見度**：`api_calls` 每次呼叫都記了 token 數（agent / model / run_id / called_at），`get_cost_summary()` 也能聚合，但沒 UI 曝光。修修要看哪個 agent 最花錢只能進 SQLite。

Phase 4 把這兩個一起收成 Bridge UI。

---

## 2. 目標 & 非目標

### In scope（V1）
- **Memory 管理頁**：列出記憶 / inline 編輯 content / 刪除 / 手動調 confidence
- **Cost dashboard 頁**：近 N 天 agent × model 用量 + 美元估算 + 時間序列
- 共用 Thousand Sunny auth（cookie `nakama_auth` 或 `X-Robin-Key`）

### Out of scope（V2 之後）
- Deck Dashboard 整合（`project_deck_dashboard_idea.md` 另立，Phase 4 完再談）
- Memory 手動新增（目前都是自動抽取，手動新增場景待確認）
- Cost 預算/告警（只做看板，不做 budget enforcement）
- 記憶匯出 / bulk operation
- 全文搜尋 UI（量小，前端 filter 就夠）
- 編輯 `memories` 表（ADR-002 Tier 3 agent run 日記）— Phase 4 **完全不碰**，未來若要管理獨立做 `/bridge/runs` timeline page。理由：Tier 3 是 agent 的 run 日記（每次 `BaseAgent.execute` 寫一筆 episodic），量級、修改需求、搜尋需求都跟 `user_memories` 不同，硬塞進同一頁會 scope 爆炸（見對話 Q4 決定）。

### Success criteria
- 修修能在瀏覽器上對每一筆 `user_memories` 做 read / edit / delete，不用碰 SQLite
- 修修能一眼看到「這週哪個 agent 花了多少錢」，精度到小時
- V1 不動現有 Phase 1-3 資料流（讀寫 API 只是 CRUD wrapper）

---

## 3. 資料現況盤點

### 3.1 `user_memories` 表（Phase 1-3，`shared/agent_memory.py`）

```
UNIQUE(agent, user_id, subject)
欄位：id, agent, user_id, type, subject, content, confidence,
      source_thread, created_at, last_accessed_at
```

已有 API：`add` / `search` / `list_all` / `forget` / `decay` / `prune` / `list_subjects_with_content` / `format_as_context`。

**已知瑕疵 Phase 4 需順手能修**：
- 舊條目 content 有主詞前綴「修修船長⋯」→ 需 inline edit
- type 被 Haiku 重判 fact ↔ preference → 需下拉選單改
- confidence 想降/升 → 需 slider 或輸入

### 3.2 `api_calls` 表（`shared/state.py:50`）

```
id, agent, run_id, model, input_tokens, output_tokens, called_at
```

已有 API：`record_api_call` / `get_cost_summary(agent, days)`（by agent×model 聚合）。

**缺**：
- 美元估算（目前只聚 token，Claude 價目表 hardcode 或 config？見 §5.F）
- 時間序列（`get_cost_summary` 只回總和，沒按日/小時分 bucket）
- Cache token 沒記（`cache_read_input_tokens`、`cache_creation_input_tokens` 目前被 `record_api_call` 丟掉）

---

## 4. 使用流程（V1）

### Flow A — 修修審記憶
1. 進 `/bridge/memory` → 預設列 Nami 的 user_memories，按 `created_at DESC`
2. 看到某筆 content「修修船長習慣早上深度工作」→ 按「編輯」→ 改成「早上偏好深度工作」
3. 看到某筆 type 抽錯 preference 抽成 fact → 下拉改 preference
4. 看到一筆很久沒用的過時記憶 → 按「刪除」→ 確認 → 消失
5. 切到 Zoro tab → 看 Zoro 對修修有哪些記憶（目前只有 Nami 在抽，Zoro 預計 0 筆）

### Flow B — 修修看花費
1. 進 `/bridge/cost` → 預設看近 7 天
2. 看到 Nami 花最多，切 30 天看長期趨勢
3. 點 Nami → drill down 看 Nami 每天花多少、哪個 model 佔比
4. 看到某天暴增 → 滑鼠 hover 看當天 agent_run 列表（連到 `agent_runs.summary`）

---

## 5. 設計決策（需修修拍板）

每條都給 A/B/C，旁邊是我的推薦理由。

### 5.A URL 結構
- **A. 分兩頁**：`/bridge/memory` + `/bridge/cost`（推薦）
- B. 合併單頁：`/bridge`（tabs 切換）
- C. 嵌入首頁：`/` 加區塊（被 Deck Dashboard 占用，先不動）

**推薦 A**：職責分離，之後 deck dashboard 可以在 `/` 嵌入 widget 連到這兩頁。

### 5.B 編輯互動模型
- A. Inline edit（click cell → 變 input → blur save）— 直觀但容易誤觸
- **B. Modal edit**（點 row → 彈 modal → 改完按 save）（推薦）
- C. 專屬 edit page（`/bridge/memory/<id>/edit`）— 對 20-30 筆記憶太重

**推薦 B**：單筆內容短（subject + content + type + confidence），modal 夠；避免 inline 誤改。

### 5.C 刪除 UX
- A. 一鍵刪除（垃圾桶 icon → 立刻消）— 危險
- **B. Confirm dialog**（刪除 → 「確定嗎？」→ 刪）（推薦）
- C. Soft delete + undo（標記刪除 → toast 顯示「已刪除，點這撤銷」）

**推薦 B**：記憶量不大，V1 不值得建 soft delete 機制；真誤刪下次對話會重抽。

### 5.D Dashboard 時間粒度
- A. 固定 7 天 daily bucket
- **B. 可切 24h/7d/30d，自動選粒度**（24h=hourly, 7d=daily, 30d=daily）（推薦）
- C. 自訂 date range picker

**推薦 B**：覆蓋 99% 場景，不用引 date picker。

### 5.E Dashboard 維度
- A. 只看 total（每天多少錢）
- **B. agent × model stacked bar**（推薦）+ top-level total
- C. 多維 drill-down（agent → model → run）

**推薦 B**：一張圖就看到「誰最花、哪個 model 佔比」；drill-down V1 先用 table 補。

### 5.F 價目表
- A. Hardcode 進 Python（`shared/pricing.py` 一個 dict）
- **B. `shared/pricing.py` dict + 環境變數 override**（推薦）
- C. DB 表（每次 API call 存當下價格）

**推薦 B**：Claude 價格變動頻率低（半年一次），dict 簡單；env var 給「合約價」場景逃生門。V1 就五六個 model 要定價。

**提醒**：目前 `record_api_call` **沒存 cache token**，要改。Opus 有 thinking token（也算 output）修修之前有踩過（見記憶 `feedback_llm_cost_estimation.md`）。V1 至少存：`input_tokens` / `output_tokens` / `cache_read_tokens` / `cache_creation_tokens`，thinking token 包含在 output 裡。

### 5.G Memory list 預設視角
- A. 全部 agent 混排
- **B. Agent tabs（Nami / Zoro / Robin / Brook / ...），預設 Nami**（推薦）
- C. Agent sidebar + 全部混排

**推薦 B**：跟心智模型對齊（「Nami 記得什麼」），也跟 `format_as_context` 的查詢 scope 一致。

### 5.H Cost 頁面時區
- **A. 全站 UTC + 副標註 Asia/Taipei 偏移**
- B. 全站 Asia/Taipei
- C. User preference

**推薦 A**：DB 存 UTC ISO8601，渲染 UTC 最安全。圖表軸可考慮 show local time hover。

### 5.I Memory 表動 schema？
- **A. 不動**（V1 只加 UI，backend 保持 Phase 1-3 合約）（推薦）
- B. 加 `updated_at`（目前只有 `last_accessed_at`，手動編輯應該有獨立時戳）
- C. 加 `notes` 欄位給修修寫備註

**推薦 A**：V1 先上；編輯後更新 `last_accessed_at` 當 updated_at 用足夠。Phase 5 再談 schema。

### 5.J Cost 估算顯示精度
- A. 美元到小數點 4 位（$0.0012）
- **B. 小於 $1 顯示 4 位，大於顯示 2 位**（推薦）
- C. 用 cents（12.3¢）

**推薦 B**：跟 Anthropic console 一致。

---

## 6. 實作分解

**假設所有 A/B/C 採推薦選項**。實作拆三個 PR：

### PR-A：Backend API（0.5 天）
- `thousand_sunny/routers/bridge.py` 新檔
  - `GET /bridge/api/memory?agent=nami` → list `user_memories`
  - `PATCH /bridge/api/memory/<id>` → update (type, subject, content, confidence)
  - `DELETE /bridge/api/memory/<id>`
  - `GET /bridge/api/cost?range=7d&agent=nami` → 含日期 bucket + cost_usd
- `shared/pricing.py` 新檔：per-model `{input_usd_per_mtok, output_usd_per_mtok, cache_read_usd_per_mtok, cache_creation_usd_per_mtok}` + env override
- `shared/state.py` 修 `record_api_call` 加 cache tokens 欄位（需 migration）
- `shared/state.py` 加 `get_cost_timeseries(agent, days, bucket)` 回日/時 bucket
- Unit tests（~15 個）：API CRUD、cost 計算、cache token 回饋

### PR-B：Memory 頁 UI（0.5 天）
- `thousand_sunny/templates/bridge/memory.html`
  - Agent tabs（硬編碼 6 個 agent）+ table（type / subject / content / confidence / last_accessed）
  - 編輯 modal + 刪除 confirm dialog
  - Vanilla JS fetch(API)，風格抄 `brook_chat.html`（dark mode、accent #6c63ff）
- `GET /bridge/memory` route handler

### PR-C：Cost dashboard 頁 UI（0.5 天）
- `thousand_sunny/templates/bridge/cost.html`
  - Range selector（24h / 7d / 30d）
  - Chart.js stacked bar chart by agent×model
  - Table：agent / model / calls / input / output / cache / cost_usd
- `GET /bridge/cost` route handler

**三個 PR 合起來 ~1.5 天，跟 Phase 4 原估 1-2 天對齊。**

---

## 7. Open Questions

1. **手動新增記憶**？V1 out of scope，但修修有時候想直接告訴 Nami「記住這件事」— 是走聊天還是走 UI？→ 建議走聊天（讓 Haiku 抽取），UI 專做審/修/刪。
2. **多個 user_id**？目前全站單一修修，user_memories 有 user_id 欄位但只有一個值。V1 寫死 `user_id = SLACK_USER_ID_SHOSHO`（從 env 讀）；如果未來加家人/朋友 slack 再擴。
3. **Cost 圖表 library**？Chart.js vs. plain SVG vs. 不畫圖只表格？建議 Chart.js（CDN，零建置，跟 `marked.js` 一樣從 cdn.jsdelivr.net）。
4. ~~**`memories` 表（ADR-002 Tier 3）要不要也接**？~~ **已決定（2026-04-19）**：V1 完全不接、UI 位置也不預留。理由：Tier 3 是 agent 的 run 日記（`BaseAgent.execute` 每次寫一筆 episodic），跟 `user_memories` UX 模型完全不同（量級大、不需編輯、需 FTS5 search + pagination）。未來若要管 Tier 3，獨立做 `/bridge/runs` timeline page，更適合 Deck Dashboard 脈絡。
5. **Auth**？跟 Brook 一致：cookie `nakama_auth` 或 header `X-Robin-Key`。不需要新 scope。

---

## 8. 修修 review checklist

- [ ] §5.A-J 十個設計決策：同意推薦？還是要改？
- [ ] §7 open questions：特別是 Q4（要不要接 `memories` 表）— 影響 IA
- [ ] §2 in/out scope：有沒有漏掉的必備功能？
- [ ] 實作分 3 PR 還是 1 PR？（三個都小，合併也行）
- [ ] 要不要先做 PR-A backend + 一個 CLI 驗證，再做 UI？

---

## 9. 明天進入 Phase 3（實作）的前置

如果 §5 全部照推薦選項走，我可以直接進 coding：
1. 開 feat branch `feat/phase-4-bridge-ui`
2. 先 PR-A（API + migration + pricing）→ 自測 → open PR → review → merge
3. 再 PR-B（Memory UI）
4. 最後 PR-C（Cost UI）
5. VPS 部署驗收

如果你要調整設計，留言在這檔案或對話裡說「5.B 改 A」即可。
