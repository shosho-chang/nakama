# ADR-003: Telegram Bot Gateway — 草帽海賊團群組

**Date:** 2026-04-13
**Status:** Approved

---

## Context

使用者想透過 Telegram 群組與 Nakama agents 即時互動。在「草帽海賊團」群組中呼喊 agent 名字就能指派任務；如果指派錯了，agent 會以自己的個性拒絕並轉介給正確的人。這是 Nakama 的第一個即時互動介面（現有介面是 Robin Web UI 和 cron）。

---

## Architecture

```
Telegram Group "草帽海賊團"
        │
        ▼
  gateway/bot.py          ← python-telegram-bot v21, long-running service
        │
  gateway/router.py       ← 1) regex 快速匹配 agent 名  2) Claude Haiku 分類 intent
        │
  gateway/handlers/*.py   ← 每個 agent 一個 handler，宣告 supported_intents
        │
  gateway/personality.py  ← 載入 prompts/{agent}/telegram.md，產生角色語氣回覆
        │
  shared/*                ← 複用既有 obsidian_writer, anthropic_client, state, events
```

新增 `gateway/` 為頂層 package（與 `agents/`、`shared/` 平行），因為它是通訊層，不是 agent。

---

## Phase 1 — MVP：Nami 任務建立 + 基本路由

### 新增檔案

| 檔案 | 用途 |
|------|------|
| `gateway/__init__.py` | package init |
| `gateway/__main__.py` | `python -m gateway` 入口 |
| `gateway/bot.py` | python-telegram-bot Application 設定、message handler、auth filter |
| `gateway/router.py` | agent 名稱 regex 匹配 + intent 關鍵字匹配（Phase 1 不用 Claude） |
| `gateway/handlers/__init__.py` | handler registry（自動載入所有 handler） |
| `gateway/handlers/base.py` | `BaseHandler` ABC：`agent_name`, `supported_intents`, `handle()`, `can_handle()` |
| `gateway/handlers/nami.py` | `create_task`, `list_tasks` — 用 `shared/obsidian_writer.py` 寫 TaskNotes |
| `gateway/personality.py` | 載入角色 prompt，呼叫 Claude 產生角色語氣回覆 |
| `prompts/gateway/route.md` | intent 分類 prompt（Phase 2 才用） |
| `prompts/nami/telegram.md` | Nami 的角色設定 + 回覆範例 |

### 修改檔案

| 檔案 | 變更 |
|------|------|
| `pyproject.toml` | dependencies 加 `python-telegram-bot>=21.0` |
| `requirements.txt` | 加 `python-telegram-bot>=21.0` |
| `.env.example` | 加 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| `config.yaml` | 加 `gateway.telegram` 區段 |

### Message Flow (Phase 1)

```
User: "Nami，下週三看牙醫"
  → bot.py: 驗證 chat_id
  → router.py: regex 匹配 "Nami" → target_agent="nami"
  → router.py: 關鍵字匹配 → intent="create_task"
  → handlers/nami.py: 呼叫 Claude 解析自然語言日期 + 任務內容 → 寫入 vault
  → personality.py: 載入 Nami prompt → Claude 產生角色回覆
  → bot.py: 發送回覆到群組
```

### Agent 名稱匹配（零 LLM 成本）

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

### TaskNotes 格式

```yaml
---
title: 看牙醫
status: to-do
priority: normal
tags: [task]
scheduled: 2026-04-16
dateCreated: 2026-04-13T14:00:00.000Z
dateModified: 2026-04-13T14:00:00.000Z
預估🍅: 1
✅: false
---
```

### 成本控制

- 名稱匹配：regex，零成本
- 任務解析：1 次 Haiku call（解析日期+內容）
- 角色回覆：1 次 Haiku call（短回覆，max_tokens=256）
- 每則訊息最多 2 次 Haiku call ≈ ~$0.001

---

## Phase 2 — 錯誤轉介 + 更多 Agent

### 新增

- `gateway/handlers/zoro.py` — 委派 `agents/zoro/keyword_research.research_keywords()`
- `gateway/handlers/robin.py` — 委派 `agents/robin/kb_search.search_kb()`
- `gateway/handlers/franky.py` — 系統狀態查詢
- `prompts/{zoro,robin,franky}/telegram.md`
- Claude Haiku 路由（當 regex 無法判斷 intent 時）

### 錯誤轉介 Flow

```
User: "Nami，幫我做關鍵字調查"
  → router: target=nami, intent=keyword_research
  → nami.can_handle("keyword_research") → False
  → nami.suggest_redirect("keyword_research") → "zoro"
  → personality.py: 產生 Nami 拒絕訊息 + Zoro 接手訊息
  → zoro.handle("keyword_research", params)
  → 群組收到兩條訊息：
    1. Nami: "關鍵字調查不是我的守備範圍～那是 Zoro 的專長！"
    2. Zoro: "是的，船長！我來查。"（附帶結果）
```

### Intent → Agent 對照表

| Intent | Agent | 來源 |
|--------|-------|------|
| create_task, list_tasks, schedule_query | Nami | 新建 |
| keyword_research, trend_check | Zoro | 既有 `agents/zoro/keyword_research.py` |
| kb_search, kb_query | Robin | 既有 `agents/robin/kb_search.py` |
| system_status, cost_summary | Franky | 既有 `agents/franky/reporter.py` |

---

## Phase 3 — 部署 + 完善

- `nakama-telegram.service` — systemd service（同 `nakama-web.service` 模式）
- 對話 context（SQLite `telegram_messages` 表）支援 follow-up
- Rate limiting + daily budget
- 所有 agent 的 personality prompt
- 群組歡迎訊息 + `/help` 指令

---

## 開發工具

Claude Code Telegram Plugin（https://claude.com/plugins/telegram, 44K+ installs）可作為開發期間的測試輔助。
不取代 gateway 架構：plugin 僅在 Claude Code 活躍時運作，production 仍需 VPS 上的持久服務。

---

## 複用的既有模組

| 模組 | 路徑 | 用途 |
|------|------|------|
| Claude API | `shared/anthropic_client.py` | `ask_claude()`, `ask_claude_multi()`, cost tracking |
| Vault I/O | `shared/obsidian_writer.py` | `write_page()`, `read_page()`, `list_files()` |
| Prompt 管理 | `shared/prompt_loader.py` | `load_prompt()` 自動注入 shared partials |
| Event Bus | `shared/events.py` | `emit()` 記錄 gateway 操作 |
| Config | `shared/config.py` | `load_config()`, `get_vault_path()` |
| Keyword Research | `agents/zoro/keyword_research.py` | `research_keywords(topic)` (Phase 2) |
| KB Search | `agents/robin/kb_search.py` | `search_kb(query, vault_path)` (Phase 2) |
