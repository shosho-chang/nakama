# ADR-004: Slack Gateway — Nakama Agents 即時互動介面

**Date:** 2026-04-15
**Status:** Approved
**Supersedes:** [ADR-003](ADR-003-telegram-gateway.md) (Telegram Gateway)

---

## Context

使用者需要即時互動介面與 Nakama agents 溝通。ADR-003 原規劃用 Telegram，但評估後改用 Slack：

- **Slash commands** 天然適合 agent 指令觸發
- **Channels + threads** 支援分主題組織和 inter-agent 溝通
- **Socket Mode** 不需公開 URL/SSL，適合 VPS 部署
- ADR-003 尚未實作，遷移成本為零

---

## Architecture

```
Slack Workspace
  #nakama    #nami    #zoro    #robin    #franky
      │         │        │        │          │
      └─────────┴────────┴────────┴──────────┘
                         │
              gateway/bot.py (Slack Bolt, Socket Mode)
                         │
            ┌────────────┼────────────┐
            │            │            │
     gateway/router.py  gateway/     gateway/
      (3-tier routing)  event_bridge  formatters
                         (Phase 2)    (Block Kit)
            │
     gateway/handlers/*.py
            │
     shared/* + agents/*
```

`gateway/` 為頂層 package（與 `agents/`、`shared/` 平行），是通訊層，不是 agent。

---

## 路由設計（三層，成本遞增）

| 層級 | 機制 | 成本 | 適用 |
|------|------|------|------|
| Tier 1 | Slash command 直達 | $0 | `/nami`, `/zoro` 等 |
| Tier 2 | Regex alias + keyword intent | $0 | @mention、自然語言 |
| Tier 3 | Claude Haiku 分類 | ~$0.0005 | Tier 2 無法判斷時 |

### Agent 名稱別名

```python
AGENT_ALIASES = {
    "nami": "nami", "娜美": "nami", "航海士": "nami",
    "zoro": "zoro", "索隆": "zoro", "劍士": "zoro",
    "robin": "robin", "羅賓": "robin", "考古學家": "robin",
    "franky": "franky", "佛朗基": "franky", "船匠": "franky",
    "brook": "brook", "布魯克": "brook", "音樂家": "brook",
    "usopp": "usopp", "騙人布": "usopp", "狙擊手": "usopp",
    "sanji": "sanji", "香吉士": "sanji", "廚師": "sanji",
}
```

### Slash Commands

| Command | Agent | 功能 |
|---------|-------|------|
| `/nami <text>` | Nami | 任務 CRUD |
| `/zoro <topic>` | Zoro | 關鍵字研究 |
| `/robin <query>` | Robin | KB 搜尋 |
| `/franky [status]` | Franky | 系統狀態 |
| `/brook <topic>` | Brook | 內容創作 |
| `/nakama <text>` | 自動路由 | 自動判斷 agent |

---

## Inter-Agent Communication

透過 `shared/events.py` event bus + EventBridge 背景執行緒：

1. Agent 完成工作 → `emit()` 事件（可附帶 `suggest_handoff`）
2. EventBridge `consume("slack_gateway", ...)` poll 事件
3. 格式化後 post 到對應 Slack 頻道
4. Handoff 訊息同時顯示在 `#nakama` 主頻道

**不需修改現有 agent 程式碼** — event bus 的 `event_consumptions` 表支援多消費者。

---

## Phase 1 — MVP：Nami 任務管理 + 基本路由

### 新增檔案

| 檔案 | 用途 |
|------|------|
| `gateway/__init__.py` | Package init |
| `gateway/__main__.py` | `python -m gateway` 入口 |
| `gateway/bot.py` | Slack Bolt App, Socket Mode |
| `gateway/router.py` | Tier 1 + Tier 2 路由 |
| `gateway/formatters.py` | Block Kit 訊息格式 |
| `gateway/handlers/__init__.py` | Handler registry |
| `gateway/handlers/base.py` | BaseHandler ABC |
| `gateway/handlers/nami.py` | `create_task`, `list_tasks` |
| `prompts/nami/slack.md` | Nami Slack 角色設定 |
| `prompts/nami/parse_task.md` | 自然語言 → 任務結構化 |

### 修改檔案

| 檔案 | 變更 |
|------|------|
| `pyproject.toml` | 加 `slack-bolt`, `slack-sdk` |
| `requirements.txt` | 同上 |
| `.env.example` | 加 `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN` |
| `config.yaml` | 加 `gateway.slack` section |

---

## Phase 2 — 更多 Agents + Event Bridge

- `gateway/event_bridge.py` — 背景 poll events → post to Slack
- Zoro, Robin, Franky handlers
- Tier 3 Haiku fallback routing
- Wrong-agent redirect flow
- 各 agent Slack 角色 prompt

---

## Phase 3 — Production Hardening

- `nakama-slack.service` systemd unit
- Brook handler（thread-based 寫作）
- Rate limiting + daily cost budget
- 對話 context（SQLite `slack_messages` 表）
- App Home dashboard

---

## 與 ADR-003 的差異

| 項目 | ADR-003 (Telegram) | ADR-004 (Slack) |
|------|-------------------|-----------------|
| 平台 | Telegram Bot API | Slack Bolt + Socket Mode |
| 觸發方式 | Regex @mention | Slash commands + @mention + NL |
| 頻道結構 | 單一群組 | 主頻道 + per-agent 頻道 |
| 連線方式 | Long polling | WebSocket (Socket Mode) |
| Inter-agent | 無明確設計 | Event Bridge + handoff |
| UI 元件 | Inline keyboard | Block Kit + threads |

---

## 複用的既有模組

| 模組 | 路徑 | 用途 |
|------|------|------|
| Claude API | `shared/anthropic_client.py` | `ask_claude()`, cost tracking |
| Vault I/O | `shared/obsidian_writer.py` | `write_page()`, `read_page()` |
| Prompt 管理 | `shared/prompt_loader.py` | `load_prompt()` |
| Event Bus | `shared/events.py` | `emit()`, `consume()` |
| Config | `shared/config.py` | `load_config()` |
| Logging | `shared/log.py` | `get_logger()`, `kb_log()` |

---

## Slack App 前置設定

1. 建立 Slack App → 啟用 Socket Mode → 取得 `xapp-` token
2. Bot Token Scopes: `chat:write`, `commands`, `app_mentions:read`, `channels:history`, `channels:read`
3. 建立 slash commands: `/nami`, `/zoro`, `/robin`, `/franky`, `/brook`, `/nakama`
4. Subscribe events: `app_mention`, `message.channels`
5. Install to workspace → 取得 `xoxb-` token
6. 建立頻道並邀請 bot
