---
name: Agent Memory 設計 — 自建 SQLite 三層架構
description: 放棄 MemPalace 後的自建記憶系統：Working + Episodic（Slack）+ Semantic（SQLite）
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
