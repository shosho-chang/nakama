# Nakama 記憶系統技術文件

> 設計文件：`docs/decisions/ADR-002-memory-system-redesign.md`
> 實作日期：2026-04-11（Phase 1–3）

---

## 概覽

Nakama 記憶系統讓 AI Agent 和 Claude Code 都能跨 session 保留知識，並隨著使用累積越來越理解使用者。系統採用**三層架構**（Hot / Warm / Cold），在 token 成本與記憶深度之間取得平衡。

```
┌─────────────────────────────────────────────────────────┐
│                    使用者 / Agent                         │
└────────────┬──────────────────┬──────────────────┬──────┘
             │                  │                  │
        ┌────▼────┐       ┌────▼────┐        ┌────▼────┐
        │  Tier 1  │       │  Tier 2  │        │  Tier 3  │
        │   Hot    │       │   Warm   │        │   Cold   │
        │ 永遠載入  │       │  按需載入  │        │  搜尋取回  │
        └────┬────┘       └────┬────┘        └────┬────┘
             │                  │                  │
         CLAUDE.md         memory/*.md         SQLite FTS5
        < 250 tokens       依 agent 篩選        全文搜尋
         每次對話            每次 API call         按需查詢
```

---

## Tier 1：Hot Memory（永遠載入）

**位置**：`CLAUDE.md`（專案根目錄）

**特性**：Claude Code 每次對話自動載入，不可避免的 token 成本。

**內容原則**：只放絕對必要的項目——

| 區塊 | 內容 | 為什麼必須在 Tier 1 |
|------|------|---------------------|
| Mission | 1 行專案描述 | Claude 需要知道自己在哪個專案 |
| Vault 寫入規則 | 6 條禁令/限制 | 安全護欄，違反會破壞 Obsidian vault |
| Development | 常用指令 | Claude Code 需要知道怎麼執行/測試 |
| 記憶系統覆寫 | 記憶路徑指引 | 確保跨平台記憶指向正確位置 |

**Token 預算**：~250 tokens（壓縮前 ~600 tokens）。

---

## Tier 2：Warm Memory（按需載入）

**位置**：`memory/` 目錄（git 追蹤，跨平台同步）

**特性**：Agent 執行時根據身份載入對應記憶，注入 Claude system prompt。

### 目錄結構

```
memory/
├── shared.md                  ← 全員共用（使用者資訊、工作流程）
├── agents/
│   ├── robin.md               ← Robin 的 KB 維護偏好
│   └── franky.md              ← Franky 的週報風格規則
├── claude/                    ← Claude Code 跨平台記憶
│   ├── MEMORY.md              ← 索引（永遠載入）
│   ├── user_profile.md        ← 使用者偏好與背景
│   ├── project_*.md           ← 專案決策記錄
│   └── feedback_*.md          ← 使用者修正指令
└── .gitkeep
```

### Frontmatter Schema

所有 Tier 2 記憶檔案使用統一的 YAML frontmatter：

```yaml
---
type: semantic | episodic | procedural | user
agent: robin | franky | shared | claude
tags: [tag1, tag2, tag3]
created: 2026-04-11
updated: 2026-04-11
confidence: high | medium | low
ttl: permanent | 90d
---
```

| 欄位 | 說明 |
|------|------|
| `type` | 記憶類型（見下方「四種記憶類型」） |
| `agent` | 所屬 agent 或 `shared` / `claude` |
| `tags` | 搜尋/篩選用標籤 |
| `created` | 建立日期 |
| `updated` | 最後更新日期 |
| `confidence` | 信心度：`high`（確認）、`medium`（推測）、`low`（待驗證） |
| `ttl` | 存活時間：`permanent` 或天數如 `90d` |

### 載入邏輯

`shared/memory.py` 的 `get_context()` 負責 Tier 2 載入：

```python
get_context(agent="robin", task="ingest")
```

1. **永遠載入** `memory/shared.md`（去除 frontmatter 後的 body）
2. **永遠載入** `memory/agents/{agent}.md`
3. 合併為格式化的 system prompt 區塊
4. `task` 參數預留給未來的 tag 篩選（Phase 4）

### 兩種記憶子系統

#### Agent 記憶（`memory/agents/`）

由開發者或 agent 手動維護的規則與偏好。每個 agent 一個檔案。

**寫入方式**：
- 開發者直接編輯 `.md` 檔
- Agent 透過 `append_memory("robin", "學到 X")` 追加

**讀取方式**：
- Agent 執行時自動載入（`get_memory_context()`）
- 注入到 Claude system prompt

#### Claude 記憶（`memory/claude/`）

Claude Code 在對話中累積的跨平台記憶。透過 git 同步，取代平台特定的 `~/.claude/projects/.../memory/` 路徑。

**結構**：
- `MEMORY.md` — 索引檔，每次對話自動載入
- 各 `.md` 檔 — 具體記憶項目，按需讀取

---

## Tier 3：Cold Memory（搜尋取回）

**位置**：`state.db`（SQLite，`memories` 表 + `memories_fts` FTS5 索引）

**特性**：不自動載入，透過搜尋取回。零額外依賴（使用既有的 SQLite）。

### 資料表結構

```sql
CREATE TABLE memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent       TEXT NOT NULL,        -- 記錄者
    type        TEXT NOT NULL,        -- semantic | episodic | procedural | user
    title       TEXT NOT NULL,        -- 簡短標題
    content     TEXT NOT NULL,        -- 記憶內容
    tags        TEXT NOT NULL,        -- JSON array，如 '["ingest", "mTOR"]'
    confidence  TEXT NOT NULL,        -- high | medium | low
    source      TEXT,                 -- 來源（run_id, file_path, ...）
    created_at  TEXT NOT NULL,        -- ISO 8601 UTC
    updated_at  TEXT NOT NULL,
    expires_at  TEXT                  -- TTL，NULL = 永久
);

-- FTS5 全文索引（自動同步，透過觸發器）
CREATE VIRTUAL TABLE memories_fts USING fts5(
    title, content, tags,
    content='memories', content_rowid='id'
);
```

### 自動同步

三個 SQLite 觸發器確保 `memories` 表的任何變更都即時同步到 FTS5 索引：

| 觸發器 | 時機 | 動作 |
|--------|------|------|
| `memories_ai` | INSERT 後 | 新增 FTS5 條目 |
| `memories_ad` | DELETE 後 | 刪除 FTS5 條目 |
| `memories_au` | UPDATE 後 | 刪除舊條目 + 新增新條目 |

### 搜尋能力

FTS5 支援：
- 中文全文搜尋（已驗證）
- 英文全文搜尋
- 布林語法：`AND`、`OR`、`NOT`
- 片語搜尋：`"exact phrase"`
- 前綴搜尋：`mTOR*`

---

## 四種記憶類型

| 類型 | 定義 | 範例 | 誰寫入 | 誰讀取 |
|------|------|------|--------|--------|
| **semantic** | 領域知識、事實 | 「mTOR pathway 是...」 | Robin | 所有 agent |
| **episodic** | 事件記錄 | 「2026-04-10 Robin ingest 了 3 篇」 | Agent（自動） | Nami、Franky |
| **procedural** | 規則、做事方式 | 「concept 不超過 5 個/source」 | 開發者 + Agent | 對應 agent |
| **user** | 關於使用者 | 「修修偏好簡潔回覆」 | Claude（對話中） | Claude |

---

## API 參考

### 統一入口

```python
from shared.memory import (
    # Tier 2: Warm Memory
    get_context,          # 智能載入 → system prompt
    load_memory,          # 舊 API（向下相容）
    save_memory,          # 覆寫記憶
    append_memory,        # 追加記憶
    parse_frontmatter,    # 解析 frontmatter

    # Tier 3: Cold Memory
    remember,             # 寫入記憶到 SQLite
    search_memory,        # FTS5 全文搜尋
)
```

### `get_context(agent, task=None, max_tokens=500) → str`

Tier 2 智能載入。合併 `shared.md` + `agents/{agent}.md`，格式化為 system prompt 區塊。

```python
ctx = get_context("robin", task="ingest")
# 回傳：
# ## 共用背景知識
# （shared.md 內容）
#
# ---
#
# ## robin 的學習記憶
# （robin.md 內容）
```

### `remember(agent, type, title, content, ...) → int`

寫入一筆記憶到 Tier 3（SQLite + FTS5）。

```python
remember(
    agent="robin",
    type="episodic",
    title="Ingest: mTOR 研究",
    content="Robin 處理了一篇 mTOR pathway 研究，建立 3 個 concept",
    tags=["ingest", "article", "mTOR"],
    confidence="high",
    source="/path/to/file.md",
    ttl_days=None,           # None = 永久
)
```

| 參數 | 型別 | 必填 | 說明 |
|------|------|------|------|
| `agent` | str | 是 | 記錄者名稱 |
| `type` | str | 是 | semantic / episodic / procedural / user |
| `title` | str | 是 | 簡短標題 |
| `content` | str | 是 | 記憶內容 |
| `tags` | list | 否 | 標籤列表 |
| `confidence` | str | 否 | high / medium / low（預設 medium） |
| `source` | str | 否 | 來源識別（file path、run_id 等） |
| `ttl_days` | int | 否 | 幾天後過期（None = 永久） |

### `search_memory(query, agent=None, type=None, limit=5) → list[dict]`

FTS5 全文搜尋。自動排除已過期的記憶。

```python
results = search_memory("mTOR", agent="robin", limit=3)
# [
#   {"id": 1, "agent": "robin", "type": "episodic",
#    "title": "Ingest: mTOR 研究", "content": "...",
#    "tags": ["ingest", "mTOR"], "confidence": "high",
#    "source": "...", "created_at": "..."},
#   ...
# ]
```

### `list_memories(agent=None, type=None, limit=20) → list[dict]`

列出記憶（不需搜尋關鍵字），依建立時間倒序。

```python
all_robin = list_memories(agent="robin")
all_episodic = list_memories(type="episodic", limit=10)
```

### `parse_frontmatter(text) → (dict, str)`

解析 YAML frontmatter，回傳 `(metadata, body)`。不依賴 PyYAML。

```python
meta, body = parse_frontmatter(open("memory/agents/robin.md").read())
# meta = {"type": "procedural", "agent": "robin", "tags": [...], ...}
# body = "# Robin 記憶檔\n..."
```

---

## Agent 生命週期整合

### BaseAgent.execute() 流程

```
execute()
  ├── start_run()                    # 記錄開始（state.db）
  ├── set_current_agent()            # 設定 API cost 歸因
  ├── run()                          # 子類別實作
  │     ├── get_memory_context()     # Tier 2: 載入記憶 → system prompt
  │     └── ask_claude(prompt, system=...)
  ├── finish_run()                   # 記錄結束（state.db）
  ├── record_episodic(summary)       # Tier 3: 自動記錄事件
  └── [ERROR] remember(error)        # Tier 3: 記錄錯誤事件
```

### record_episodic() 覆寫

BaseAgent 提供預設的 episodic 記錄（agent name + summary）。子類別可 override 提供更豐富的內容：

```python
class FrankyAgent(BaseAgent):
    def record_episodic(self, summary: str) -> None:
        remember(
            agent="franky",
            type="episodic",
            title=f"Weekly Report: {self._report_info['period']}",
            content=f"Open: {self._report_info['open_tasks']}, ...",
            tags=["weekly-report", self._report_info["period"]],
            confidence="high",
        )
```

### Robin 特殊情況

Robin 的 ingest 透過 Web UI 觸發（不走 `execute()`），所以直接在 `IngestPipeline.ingest()` 結尾呼叫 `remember()`。

---

## 記憶維護

### 指令

```bash
python -m shared.memory_maintenance stats     # 查看記憶統計
python -m shared.memory_maintenance expire    # 清理 TTL 到期的記憶
python -m shared.memory_maintenance archive   # 歸檔 >90 天的低信心度記憶
python -m shared.memory_maintenance archive --days 60  # 自訂天數
```

### 維護流程

```
archive（標記過期）→ expire（實際刪除）
```

1. **`archive`**：找出超過 N 天且 `confidence=low` 的記憶，設 `expires_at = now`
2. **`expire`**：刪除所有 `expires_at < now` 的記憶（FTS5 觸發器自動同步）

建議排程：每月執行一次 `archive` + `expire`。

### stats 範例輸出

```
記憶統計：
  總數: 42（活躍: 38, 過期: 4）
  依 agent: {'robin': 25, 'franky': 8, 'claude': 5}
  依 type:  {'episodic': 30, 'procedural': 8, 'semantic': 4}
  依信心度: {'high': 35, 'medium': 5, 'low': 2}
```

---

## 跨平台策略

### 問題

Claude Code 在不同作業系統的記憶路徑不同：
- Mac: `~/.claude/projects/-Users-shosho-Documents-nakama/memory/`
- Windows: `C:\Users\shosho\.claude\projects\C--Users-shosho-Documents-nakama\memory\`

### 解法

`CLAUDE.md` 中覆寫預設行為：

> **覆寫預設行為**：不使用平台特定的 `~/.claude/projects/…/memory/` 路徑。
> 所有記憶統一存放於 repo 內的 `memory/claude/`，透過 git 跨平台共用。

切換平台後只需 `git pull`，記憶即同步。

---

## 檔案索引

| 檔案 | 職責 |
|------|------|
| `CLAUDE.md` | Tier 1: Hot Memory（永遠載入） |
| `memory/shared.md` | Tier 2: 全員共用背景知識 |
| `memory/agents/*.md` | Tier 2: 各 agent 的學習記憶 |
| `memory/claude/MEMORY.md` | Tier 2: Claude 記憶索引 |
| `memory/claude/*.md` | Tier 2: Claude 跨平台記憶項目 |
| `shared/memory.py` | 核心模組：Tier 2 載入 + Tier 3 re-export |
| `shared/state.py` | Tier 3: SQLite 表定義 + remember/search/list API |
| `shared/memory_maintenance.py` | 維護工具：expire / archive / stats |
| `agents/base.py` | Agent 生命週期整合（自動 episodic） |
| `docs/decisions/ADR-002-memory-system-redesign.md` | 架構決策記錄 |

---

## 升級路徑（Phase 4）

目前 Tier 3 使用 SQLite FTS5（零依賴、跨平台）。未來可升級為：

| 選項 | 適用場景 | 狀態 |
|------|---------|------|
| **MemPalace** | 原文保留 + 170 tokens 啟動 + MCP 原生 | 等中文支援（Zoro 追蹤中） |
| **Mem0 self-hosted** | 語意向量搜尋（pgvector） | 備選 |
| **Basic Memory** | Obsidian 原生整合 | 備選 |

升級時只需替換 `search_memory()` 的 backend，`get_context()` 和 `remember()` 介面不變。
