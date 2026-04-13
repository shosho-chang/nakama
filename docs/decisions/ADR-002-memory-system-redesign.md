# ADR-002: Memory System Redesign — 三層記憶架構

**Date:** 2026-04-11
**Status:** Proposed

---

## Context

Nakama 目前的記憶系統是純 Markdown 檔案 + 全量注入 system prompt。隨著 agent 增加和使用者累積的偏好越來越多，面臨以下問題：

1. **Token 浪費**：每次 API call 都載入全部記憶（~900 tokens/ingest），無法按需載入
2. **無法搜尋**：沒有語意搜尋，Claude 找不到相關記憶
3. **無自動學習**：agent 完成工作後不會自動記錄學到的東西
4. **跨平台斷裂**：Claude Code 在 Mac/Windows 的記憶路徑不同，對話記憶流失
5. **無壓縮機制**：記憶只增不減，長期會爆

### 研究發現

參考了社群主流做法：Claude Code 內建 4 層記憶、MCP Memory Server 生態（Basic Memory、Mem0、mcp-memory-service）、Memsearch 混合搜尋、學術三層架構（Episodic / Semantic / Procedural）。

---

## Decision

### 核心設計：三層記憶 + 四種記憶類型

```
                    ┌─────────────────────────────┐
                    │         使用者 / Agent        │
                    └──────────┬──────────────────┘
                               │
        ┌──────────────────────┼──────────────────────┐
        │                      │                      │
   ┌────▼────┐           ┌────▼────┐           ┌────▼────┐
   │  Tier 1  │           │  Tier 2  │           │  Tier 3  │
   │   Hot    │           │   Warm   │           │   Cold   │
   │ 永遠載入  │           │  按需載入  │           │  搜尋取回  │
   └─────────┘           └─────────┘           └─────────┘
   CLAUDE.md             memory/*.md            MCP Server
   rules/*.md            memory/claude/         or SQLite+FTS
   <50 tokens/agent      按 tag 篩選             語意搜尋
```

### Tier 1: Hot Memory（永遠載入）

**位置**：`CLAUDE.md` + `.claude/rules/`（未來）
**特性**：每次對話/API call 自動載入，不可避免的 token 成本
**內容**：只放「絕對必要」的東西

| 檔案 | 內容 | 預估 tokens |
|------|------|------------|
| `CLAUDE.md` | 專案身份、架構概覽、vault 寫入規則 | ~400（現有，可壓縮到 ~250） |
| `rules/agent-{name}.md` | 各 agent 的核心身份（1-2 句話） | ~30/agent |

**設計原則**：
- 嚴格控制在 **300 tokens 以下**（agent system prompt 部分）
- 只放「不變的核心身份」和「絕對不能違反的規則」
- 其餘全部下放到 Tier 2 / Tier 3

### Tier 2: Warm Memory（按需載入）

**位置**：`memory/` 目錄（repo 內，git 追蹤）
**特性**：根據當前任務的 tag/context 篩選載入，不是全量

**目錄結構**：
```
memory/
├── shared.md                  ← 全員共用背景（壓縮版）
├── agents/
│   ├── robin.md               ← Robin 的學習記憶
│   ├── franky.md              ← Franky 的學習記憶
│   ├── nami.md                ← ...
│   └── ...
├── claude/                    ← Claude Code 跨平台記憶
│   ├── MEMORY.md              ← 索引（永遠載入）
│   ├── user_profile.md        ← 修修的偏好、角色、知識背景
│   ├── feedback_*.md          ← 修修給的修正指令
│   ├── project_*.md           ← 專案決策、進度
│   └── reference_*.md         ← 外部資源指標
└── episodic/                  ← 事件記錄（自動產出）
    ├── 2026-04-10_robin_ingest.md
    ├── 2026-04-10_franky_report.md
    └── ...
```

**載入邏輯**（升級 `shared/memory.py`）：
```python
def get_context(agent: str, task: str = None, max_tokens: int = 500) -> str:
    """
    智能載入記憶：
    1. 永遠載入 shared.md（壓縮版）
    2. 永遠載入 agents/{agent}.md
    3. 根據 task tag 篩選 episodic/ 中的相關記錄
    4. 如果超過 max_tokens，自動摘要壓縮
    """
```

**Frontmatter Schema（所有記憶檔案統一格式）**：
```yaml
---
type: semantic | episodic | procedural | user
agent: robin | franky | nami | claude | shared
tags: [kb-maintenance, concept-extraction, ...]
created: 2026-04-10
updated: 2026-04-11
confidence: high | medium | low
ttl: 90d | permanent
---
```

**壓縮策略**：
- 超過 90 天且 confidence=low 的記憶 → 自動歸檔到 `memory/archive/`
- 每月執行一次記憶壓縮：合併重複項、摘要舊條目
- `max_tokens` 限制：超過時只取最新 + 最高 confidence 的條目

### Tier 3: Cold Memory（搜尋取回）

**兩個選項（依優先序）**：

#### 選項 A：SQLite + FTS5（推薦先做）

**位置**：`state.db` 新增 `memories` 表
**特性**：全文搜尋（FTS5），零依賴，跨平台

```sql
CREATE TABLE memories (
    id INTEGER PRIMARY KEY,
    agent TEXT NOT NULL,           -- robin, franky, claude, shared
    type TEXT NOT NULL,            -- semantic, episodic, procedural, user
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT,                     -- JSON array
    confidence TEXT DEFAULT 'medium',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME,          -- TTL
    source TEXT                   -- session_id, agent_run_id, etc.
);

CREATE VIRTUAL TABLE memories_fts USING fts5(
    title, content, tags,
    content='memories',
    content_rowid='id'
);
```

**搜尋 API**：
```python
def search_memory(query: str, agent: str = None, type: str = None, limit: int = 5) -> list[dict]:
    """FTS5 全文搜尋，支援 agent/type 篩選"""

def remember(agent: str, type: str, title: str, content: str, tags: list, confidence: str = "medium"):
    """記錄一筆新記憶，自動更新 FTS 索引"""
```

**優點**：
- 零額外依賴（已經用 SQLite）
- 跨平台（state.db 已存在）
- FTS5 內建中文支援（ICU tokenizer）
- 可後續升級到向量搜尋

#### 選項 B：MCP Memory Server（Tier 3 進階版，未來升級）

等 agent 數量增加、記憶量超過 FTS5 能力時，升級到：
- **Basic Memory**（如果 Obsidian 整合是首要） 
- **Mem0 self-hosted**（如果語意搜尋是首要）
- **MemPalace**（`milla-jovovich/mempalace`）— 目前最強候選（LongMemEval 96.6% vs Mem0 ~85%，原文保留不有損，啟動僅 170 tokens，MCP 原生 19 工具）。但 2026-04-11 查核時尚無中文支援且專案僅 5 天大。等中文語意搜尋完善後優先評估，可能取代 Mem0。Franky 每週追蹤中（`config/franky-watch-mempalace.yaml`）。

升級路徑：`search_memory()` 介面不變，只換 backend。

---

## 四種記憶類型

| 類型 | 定義 | 範例 | 誰寫 | 誰讀 |
|------|------|------|------|------|
| **Semantic** | 領域知識、事實 | 「mTOR 是...」「Rapamycin 屬於 intervention」 | Robin | All |
| **Episodic** | 事件記錄 | 「2026-04-10 Robin ingest 了 3 篇文章」 | Agent（自動） | Nami, Franky |
| **Procedural** | 怎麼做事 | 「concept 頁面不超過 5 個/source」 | 修修（手動）+ Agent（學習） | 對應 agent |
| **User** | 關於修修 | 「偏好簡潔回覆、不要 emoji」 | Claude（對話中） | Claude |

---

## Agent 生命週期整合

```python
class BaseAgent:
    def execute(self):
        set_current_agent(self.name)
        
        # ── 記憶載入（升級版）──
        hot = self.get_hot_memory()         # Tier 1: 核心身份
        warm = self.get_warm_memory(task)    # Tier 2: 按需篩選
        system_prompt = f"{hot}\n\n{warm}"
        
        # ── 執行 ──
        result = self.run(system_prompt)
        
        # ── 自動記錄（新增）──
        self.record_episodic(result)         # Tier 2/3: 寫入事件記錄
        self.update_learnings(result)        # Tier 2/3: 提取新學到的規則
        
        record_run(self.name, result)
```

---

## Claude Code 記憶（修修 ↔ Claude 對話）

### 目標

讓 Claude 越來越懂修修，減少每次重頭解釋的 token 浪費。

### 記憶來源

| 觸發時機 | 記憶類型 | 範例 |
|----------|---------|------|
| 修修糾正 Claude | feedback | 「不要加 emoji」「用繁中回覆」 |
| 修修描述自己 | user | 「我是 data scientist」「Go 寫 10 年」 |
| 專案決策 | project | 「MemPalace 暫不整合」「Franky 改為工程總監」 |
| 修修提到外部資源 | reference | 「bug 追蹤在 Linear INGEST 專案」 |

### 儲存位置

- **Tier 2**：`memory/claude/` 下的 `.md` 檔案（git 同步跨平台）
- **Tier 3**：`state.db` 的 `memories` 表（搜尋用）

### Token 節省機制

| 機制 | 效果 |
|------|------|
| `MEMORY.md` 索引 | Claude 只讀索引（~50 tokens），需要時才讀具體檔案 |
| Frontmatter tags | 只載入跟當前任務相關的記憶 |
| 壓縮策略 | 舊記憶自動摘要，降低 token 佔用 |
| Confidence 排序 | 優先載入高信心度記憶 |

---

## 實作計畫

### Phase 1：基礎升級（立即）

1. **升級 `shared/memory.py`**
   - 新增 `get_context(agent, task, max_tokens)` — 智能載入
   - 新增 frontmatter 解析（讀取 tags, confidence, ttl）
   - 保留舊 API 向下相容（`load_memory` 改為呼叫 `get_context`）

2. **統一 Frontmatter Schema**
   - 更新 `memory/shared.md`、`robin.md`、`franky.md` 加上 frontmatter
   - 更新 `memory/claude/` 下所有檔案

3. **重整目錄結構**
   - `memory/agents/` — agent 記憶
   - `memory/claude/` — Claude ↔ 修修記憶
   - `memory/episodic/` — 事件記錄

4. **壓縮 CLAUDE.md**
   - 移除可下放到 Tier 2 的內容
   - 目標：< 250 tokens

### Phase 2：搜尋層（本週）

5. **`state.db` 新增 `memories` + `memories_fts` 表**
6. **`shared/memory.py` 新增 `search_memory()` + `remember()`**
7. **Robin ingest 結束後自動呼叫 `remember()` 記錄事件**
8. **Franky report 結束後自動呼叫 `remember()` 記錄事件**

### Phase 3：自動學習（下週）

9. **BaseAgent 生命週期加入 `record_episodic()` 和 `update_learnings()`**
10. **Claude Code 對話結束時自動提取 feedback/user/project 記憶**
11. **記憶壓縮 cron：每月摘要舊記憶、歸檔過期記憶**

### Phase 4：進階（未來）

12. **MCP Memory Server 整合**（Basic Memory 或 Mem0）
13. **向量搜尋**（替換 FTS5）
14. **記憶儀表板**（Obsidian DataviewJS 查詢 state.db）

---

## Consequences

### 正面
- Token 節省 30-50%（從全量載入改為按需篩選）
- Agent 越用越聰明（自動學習機制）
- Claude 越來越懂修修（跨平台持久記憶）
- 可搜尋的結構化知識庫
- 升級路徑清晰（SQLite → MCP → Vector DB）

### 風險
- Phase 1 需要重構現有 memory 載入邏輯（影響 Robin、Franky）
- FTS5 中文分詞品質取決於 ICU tokenizer 設定
- 自動學習可能記錄錯誤的規則 → 需要修修定期審核

### 不做的事
- 不建自己的向量資料庫（先用 FTS5，夠用再說）
- 不做 real-time 記憶同步（git pull/push 就夠）
- 不替 stub agent 預先建立記憶檔（等實作時再建）

---

## Notes

- 此決策在 2026-04-11 與修修討論後提出
- 研究來源：Claude Code 官方文件、Memsearch、Mem0、Basic Memory、MCP Memory Server 生態、學術記憶架構（MIRIX、AriGraph）
- 現有 API（`load_memory`, `append_memory`）保持向下相容
