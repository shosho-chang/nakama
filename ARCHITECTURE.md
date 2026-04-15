# Architecture

## 系統概覽

Nakama 是一個多 Agent AI 系統，為單一內容創作者（Owner）設計。每個 Agent 負責工作流程的一個環節，透過共用的基礎設施協同運作。

```
Owner
  │
  ├── Thousand Sunny ──► Robin ──► Obsidian Vault (KB/)
  │                                    │
  ├── Cron ──► Zoro ───────────────────┤
  │        ──► Nami ───────────────────┤
  │        ──► Sanji ──────────────────┤
  │        ──► Franky ─────────────────┤
  │                                    │
  └── Manual ──► Usopp ──► WordPress / YouTube / Fluent CRM
              ──► Brook ──► 各平台格式輸出
```

---

## 核心模組（shared/）

### `anthropic_client.py`
Claude API 的統一入口。所有 Agent 透過 `ask_claude()` 呼叫 Claude，自動處理：
- API 重試（指數退避，最多 3 次）
- Token 計費（記錄至 `state.db`）
- Agent 歸因（`set_current_agent()` 設定 context）

### `base.py`（agents/base.py）
所有 Agent 的抽象基底類別。定義統一的 lifecycle：

```
BaseAgent.execute()
  ├── set_current_agent()    # 設定計費歸因
  ├── load_memory()          # 注入跨 session 記憶
  ├── run()                  # 子類別實作
  └── record_run()           # 記錄執行結果至 state.db
```

### `state.py`
SQLite 狀態管理。主要資料表：

| 資料表 | 用途 |
|--------|------|
| `files_processed` | Robin 已處理的檔案（防止重複處理） |
| `files_read` | Robin 閱讀標註狀態 |
| `agent_runs` | 各 Agent 執行紀錄（含 token 計費） |
| `api_calls` | Claude API 呼叫明細 |
| `agent_events` | 跨 Agent 事件傳遞 |

### `memory.py`
跨 session 記憶系統。記憶以 Markdown 檔案儲存於 `memory/`：

- `memory/shared.md`：全員共用背景（Owner 偏好、領域知識）
- `memory/[agent].md`：各 Agent 的學習紀錄

每次執行時自動注入 Claude system prompt。

### `events.py`
Agent 間的事件傳遞機制。基於 SQLite `agent_events` 表：
- `emit(agent, event_type, payload)` — 發送事件
- `consume(agent, event_type)` — 消費事件（標記已讀）
- `peek(agent, event_type)` — 查看未消費事件

### `prompt_loader.py`
集中式 prompt 管理。從 `prompts/` 目錄載入，自動插入 shared partials：
- `prompts/shared/domain.md` — 領域知識（longevity、wellness）
- `prompts/shared/writing-style.md` — 語言與格式規範
- `prompts/shared/vault-conventions.md` — 知識庫頁面規範

### `obsidian_writer.py`
Obsidian vault 的讀寫介面：
- `write_page(path, content)` — 寫入頁面（含 frontmatter）
- `read_page(path)` — 讀取頁面
- `append_to_file(path, content)` — Append（用於 log.md）
- `list_files(directory)` — 列出目錄內容

---

## Robin：Knowledge Base Agent

目前唯一完成的 Agent，也是系統最核心的部分。

### 工作流程

```
Inbox/kb/ 檔案
    │
    ▼
[Web UI] 使用者選擇檔案 + 類型
    │
    ▼
複製至 KB/Raw/[type]/
    │
    ▼
[可選] 使用者在 Reader UI 標記重點與筆記
    │
    ▼
Claude: 產出 Source Summary → KB/Wiki/Sources/
    │
    ▼
使用者輸入引導方向
    │
    ▼
Claude: 提取 Concepts & Entities → 使用者審核
    │
    ▼
Claude: 建立/更新 Concept & Entity 頁面 → KB/Wiki/
    │
    ▼
更新 KB/index.md + KB/log.md
```

### Thousand Sunny — Web Server（`thousand_sunny/`）
FastAPI + Jinja2，部署為 systemd service（`thousand-sunny.service`）。
獨立 web server 模組，每個 agent 有自己的 router：
- `routers/robin.py` — KB ingest UI、Reader、KB search（16 routes）
- `routers/zoro.py` — Keyword research（1 route）
- `routers/brook.py` — Article composition chat（6 routes）
- `auth.py` — 共用認證（HMAC cookie + API key）
- SSE 即時進度回饋
- Reader UI：支援 `==highlight==` 標記與 `> [!annotation]` 筆記

---

## 資料流

```
Obsidian Vault（本機 Windows）
    ↕ Syncthing 雙向同步
VPS /home/nakama/LifeOS/
    ↑ Robin 寫入 KB/
    ↑ Nami 寫入 AgentBriefs/
    ↓ Robin 讀取 Inbox/kb/

VPS /home/nakama/data/state.db
    ← 所有 Agent 讀寫（執行狀態、計費、事件）
```

---

## 設計決策

重大設計決策記錄於 [docs/decisions/](docs/decisions/)：

| ADR | 決策 |
|-----|------|
| [ADR-001](docs/decisions/ADR-001-agent-role-assignments.md) | Agent 職責分配（v0.1.0 → v0.4.0 重新安排） |

---

## 技術選型

| 項目 | 選擇 | 原因 |
|------|------|------|
| LLM | Claude Sonnet（claude-sonnet-4-6） | 品質與成本平衡 |
| Web Framework | FastAPI + Uvicorn | 異步、型別安全、SSE 支援 |
| 資料庫 | SQLite | 單機部署，零額外依賴 |
| 設定 | YAML + dotenv | 組態與密鑰分離 |
| 部署 | systemd + crontab | VPS 標準做法，不需 Docker |
| 同步 | Syncthing | 本機 vault ↔ VPS 雙向同步 |
