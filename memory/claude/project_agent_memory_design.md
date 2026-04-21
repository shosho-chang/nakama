---
name: Agent Memory 設計 — 自建 SQLite 三層架構
description: Phase 1-4 全完成（Phase 4 Bridge UI + hub 2026-04-20 部署）；三層架構 + Bridge /bridge/memory + /bridge/cost + /bridge landing
type: project
tags: [memory, agent, sqlite, nami, cross-agent]
created: 2026-04-19
originSessionId: 6dc774b2-8ee7-4655-b691-eebe19832245
---
## 背景

MemPalace 2026-04-19 re-eval 結論「放棄等」（見 `project_mempalace_evaluation.md`），改自建。

## 三層記憶架構

| 層級 | 內容 | 儲存 | 壽命 |
|---|---|---|---|
| Working | 當前對話 | ConversationStore in-memory | 30 分鐘 |
| Episodic | 最近的 raw 對話 | Slack API on-demand fetch | 90 天（Slack 保留） |
| Semantic | 抽取出的事實/偏好/決策 | SQLite `agent_memory` | 永久（帶 decay） |

## SQLite Schema

```sql
CREATE TABLE agent_memory (
  id INTEGER PRIMARY KEY,
  agent TEXT,           -- 'nami' / 'zoro' / 'robin' / ...
  user_id TEXT,         -- Slack user ID
  type TEXT,            -- 'preference' / 'fact' / 'decision' / 'project'
  subject TEXT,         -- 索引用（例：'工作時段'、'喜好 content type'）
  content TEXT,         -- 實際內容
  confidence REAL,      -- 0-1，用於 decay
  source_thread TEXT,   -- 回溯原對話
  created_at TIMESTAMP,
  last_accessed_at TIMESTAMP
);
CREATE INDEX idx_memory_lookup ON agent_memory(agent, user_id, subject);
```

## 四階段實作計畫

### Phase 1：SQLite Store（1 天）
- `shared/agent_memory.py`：`add` / `search` / `decay` / `forget`
- 多 agent 共用，透過 `agent` 欄位隔離

### Phase 2：自動抽取（1-2 天）
- 對話 `end_turn` 後，背景呼叫 **Haiku 4.5**（便宜）提取事實
- Prompt：「從這段對話找出使用者的偏好/決策/重要事實，忽略閒聊，JSON 回」
- 去重：`subject` 命中就 update，否則 insert

### Phase 3：Context 注入（1 天）
- 新對話開始時查該 `user_id` + `agent` top 20 memories（按 `confidence × recency`）
- 組成「你記得關於修修的事」block 注入 user message（保 system prompt 可 cache）
- Prompt caching 處理重複記憶區塊

### Phase 4：Bridge UI（1-2 天）
- 列出每個 agent 的記憶、手動編輯/刪除
- 與 `project_agent_cost_tracking.md` 同一個 dashboard

## 總預估

5-6 個人日。

## 狀態：Phase 1-3 已部署 VPS 實測通過（2026-04-19）

**驗證項目全部通過**：
- 自動抽取（Haiku 4.5 on end_turn）
- Subject 去重（list_subjects_with_content 注入 prompt）
- Content 合併不覆蓋（merge rule in prompt）
- Context 注入對話（format_as_context at handle() only, 不 cacheable 避開 system prompt）
- 背景 daemon thread 不 block 主流程

**VPS commit**：62007b8

**已知小瑕疵**（Phase 4 前不影響使用）：
- 舊條目 content 有多餘主詞前綴（「修修船長⋯」）
- type 可能被 Haiku 重判（fact ↔ preference）
- ConversationStore 仍是 in-memory，restart 丟失 active conversations（記憶本身持久 OK）

## Phase 4 進度（2026-04-20）

**PR-A backend merged（PR #41, commit 73a064c）**

- PRD: `docs/prds/phase-4-bridge-ui.md`（Approved for implementation，10 個設計決策 A-J 全採推薦選項）
- 實作：`shared/pricing.py`（價目表 + env override）+ `api_calls` schema migration（補 cache_read/write_tokens 欄位）+ `shared/agent_memory.py` get/update/list_agents_with_memory + `thousand_sunny/routers/bridge.py`（memory CRUD + cost overview API）
- 順手修掉隱藏 bug：`record_api_call` 之前把 Anthropic `cache_read_input_tokens` / `cache_creation_input_tokens` 丟掉，現在會存
- 測試：59 新 + 全 457 pass，code review 7 issue 全 < 80 無 blocker

**Q4 關鍵設計決定**：Tier 3 `memories` 表（ADR-002 agent run 日記）**不進本頁**，UI 位置也不預留。理由：量級（幾千筆 vs <50）、修改需求、搜尋需求完全不同。未來若要管獨立做 `/bridge/runs` timeline page，適合進 Deck Dashboard。

**剩下的工作**：
- PR-B：Memory 頁 UI（Jinja template + agent tabs + 編輯 modal + delete confirm，~0.5 天）
- PR-C：Cost dashboard 頁 UI（range selector + Chart.js stacked bar，~0.5 天）
- VPS 部署（PR-C merge 後一起；`api_calls` migration 自動跑，舊資料 cache 欄位 default 0 相容）
- Tech debt follow-up（review 抓到但未擋 merge）：
  1. `agent_memory.update` 加 `conn.rollback()` after IntegrityError
  2. `MemoryUpdate.type` 改 `Literal[]` + `agent_memory.add/update` enum check
  3. `shared/pricing.py` 模組 docstring lookup order 跟 function docstring 衝突
  4. `get_cost_summary` docstring 漏新加的 cache 欄位

## 2026-04-20 — Phase 4 全部完成

- **PR #42 Memory UI**（commit e39edb2）：`/bridge/memory` + agent tabs + edit modal + delete confirm；code-review 92/100
- **PR #44 Cost UI**（commit 717069f）：`/bridge/cost` + 24h/7d/30d range + Chart.js stacked bar + breakdown table；code-review 88/100
- **PR #45 Bridge Hub**（commit a28c3eb）：`/bridge` landing 4-tile hub (Memory/Cost/Brook/Robin)，Robin tile 在 `DISABLE_ROBIN=1` 下 greyed out；解決 header「Home」誤連到 Brook 的問題
- VPS 全部部署並 smoke-test 通過（thousand-sunny restart）；近 7d cost $2.93（Nami Sonnet 66 calls/$0.30）

**踩到的坑**：
- VPS `.env` 沒設 `SLACK_USER_ID_SHOSHO`，`_default_user_id()` fallback 到 `"shosho"` 查不到 Nami 真實 user_id `U05F841H127`。修修手動補了 env 後正常。未來 agent 加 user-scoped filter 時要檢查 VPS 是否有對應 env
- 合 PR-B 時 `--delete-branch` 連帶把 PR-C 的 base branch 砍掉，PR-C 變 CLOSED / CONFLICTING 無法 reopen，必須 rebase + 另開新 PR #44

**Tier 3 `memories` 表**仍未接 UI（PRD §2 Q4 凍結的決定）；未來若要管改做獨立 `/bridge/runs` timeline page

**Deck Dashboard**：Bridge hub 當作最小 placeholder，等其他 agent 成熟再決定 hub 要不要升級成完整 dashboard（project_deck_dashboard_idea.md）

## 2026-04-21 — Direction B Instrument Panel 全面重設計

- **PR #65**（commit ff55bd4）：Bridge UI 3 頁全面套用 Direction B "Instrument Panel" 設計語言
  - `bridge.py`：新增 `AGENT_ROSTER` 常數（9 agents 靜態定義）+ `GET /bridge/api/agents` endpoint（今日 token/runs 統計）
  - `index.html`：Hub → 9-agent instrument panel dashboard（state chips、signal gauge、right-side detail drawer 140ms slide-in）
  - `memory.html`：Left agent rail 220px、outline kind chips、黑色 chassis modal header
  - `cost.html`：移除 Chart.js，改用純 CSS flex stacked bars + 絕對定位 gridlines、agent×model matrix table
  - 設計語言：骨白 `#e8e6e1` + 黑色 chassis `#1a1a1a` + signal-orange `#ff5a1f` 僅 active 狀態
  - Google Fonts：Space Grotesk + Noto Sans TC + JetBrains Mono；全數字 `font-variant-numeric: tabular-nums`

- **測試修復**：`test_bridge_index_renders_html` + `test_bridge_index_hides_robin_when_disabled` 更新斷言
  - `"Nakama Bridge"` → `"NAKAMA / BRIDGE"`；`href="/brook/chat"` → `"'/brook/chat'"` (JS string)
  - index.html 加入 Jinja2 `robin_enabled` 條件：`AGENT_URLS.robin` 動態切換 + JS comment 輸出 `DISABLE_ROBIN=0/1`

- **已知小問題**（細節待改，不影響功能）：很多 Bridge UI 視覺細節，修修說「先這樣」

- **VPS 部署**：PR merge 後 thousand-sunny restart，smoke test 需在瀏覽器手動確認
