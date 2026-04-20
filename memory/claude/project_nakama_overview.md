---
name: Nakama 專案總覽
description: Agent 全員任務、工作流程、狀態、基礎設施、VPS 部署、Tech Stack 完整概覽
type: project
tags: [nakama, overview, agents]
created: 2026-04-09
updated: 2026-04-17
confidence: high
ttl: permanent
originSessionId: ecac2e9b-d409-4922-b30f-4270e46d6df0
---
Nakama 是部署在 VPS 上的 AI agent team，寫入 LifeOS Obsidian vault（透過 Syncthing 同步）。

## Agent 全員任務（2026-04-09 確定版）

| 船員 | 角色 | 核心任務 | 排程 |
|------|------|---------|------|
| Robin | 考古學家 | 攝入 Inbox/kb/ → Source Summary + Concept + Entity → 維護 KB/Wiki | 02:00 每天 |
| Nami | 航海士 | Morning Brief + 各平台數據週報 + 邀約報價草稿 | 07:00 每天；週一數據 |
| Zoro | 劍士 | KOL/PubMed/Google Trends 情報 | 06:00 每天 |
| Sanji | 廚師/外場 | 自由艦隊社群管理、監控未回覆貼文、活動策劃（Fluent Community） | 監控每小時 |
| Usopp | 狙擊手 | 內容精準發布（WordPress/YouTube/社群）+ 電子報（Fluent CRM） | 核准後觸發 |
| Brook | 音樂家/Composer | 素材（文章/腳本）→ Compose 各平台格式 → 交 Usopp 發布 | 手動觸發 |
| Franky | 船匠/工程總監 | 工程進度追蹤、系統健康報告、套件更新監測 | 週一 01:00 |
| Chopper | 醫生 | 自由艦隊會員個人健康顧問（待 MemPalace 整合後啟動） | webhook 觸發 |

## 工作流程

```
Zoro ──情報──▶ Robin ──知識──▶ Brook ──格式──▶ Usopp ──▶ 各平台發布
Sanji ──社群狀態──▶ Nami ◀──當日產出──────────────────────┘
Franky ──系統健康──▶ Nami
Chopper ──一對一──▶ 自由艦隊會員（獨立運作）
```

## 狀態（2026-04-20 更新）

- Robin：✅ 完成（含 /kb/research endpoint + 步驟 4 已改走 Gemini facade，PR #53 待 merge）
- 基礎設施 v0.4：✅ 完成（PR #2 merged）
- Franky：✅ 完成（工程週報系統）
- ADR-002 記憶系統：✅ Phase 1-3 完成（PR #4 merged）
- 基礎建設 #1-2：✅ pyproject.toml + CI/CD + Ruff + pre-commit
- ADR-002 Phase 4（MCP Memory Server）：⏸ 等 MemPalace 中文支援
- Zoro Keyword Research v2：✅ 完成並部署
- **Thousand Sunny**：✅ 完成並部署
- **Brook Phase 1：✅ 完成**
- **Nami**：✅ Slack bot + 11 tools + Google Calendar + task sync 全 merged + VPS deployed
- **Sanji**：✅ 第一版上線（2026-04-20）— persona prompt + `SanjiHandler`（同 Slack app、Grok 聲音）+ brainstorm 參與者身份。社群監控 / Fluent Community 管理功能留 follow-up
- **多 Model 多 Agent 架構**：✅ 4/8 步完成（router / xAI / brainstorm P1 / Gemini），詳見 [project_multi_model_architecture.md](project_multi_model_architecture.md)
- **Brainstorm orchestrator**：✅ P1 完成 — `@nakama brainstorm <主題>` 選 2 agents 並行跑 + Nami synthesizer。P2（Zoro agent-initiated）/ P3（夜間 async）待開
- Chopper：🔒 待 MemPalace 整合條件滿足後啟動

## 基礎設施 v0.4

| 模組 | 檔案 | 功能 |
|------|------|------|
| Event Bus | `shared/events.py` | agent 間傳遞事件 |
| 記憶系統 | `shared/memory.py` + `memory/` | 三層架構（Tier 1-3） |
| Retry | `shared/retry.py` | API 指數退避重試 |
| Cost tracking | `shared/anthropic_client.py` | token 用量記錄 |
| Prompt 模組化 | `shared/prompt_loader.py` + `prompts/` | shared partials |

## 部署架構（2026-04-16 更新）

| 服務 | VPS | 本機 | 說明 |
|------|-----|------|------|
| Robin (Ingest + Reader) | ❌ | ✅ | 本機有 GPU 跑 Gemma 4，`DISABLE_ROBIN=1` |
| Brook (聊天) | ✅ | — | |
| Zoro (Keyword Research) | ✅ | — | |
| KB Research API | ✅ | — | |

- systemd：`thousand-sunny`（Nakama）+ `cloudflared`（HTTPS tunnel）
- 對外 URL：`https://nakama.shosho.tw`（Cloudflare Tunnel，VPS 不開 inbound port）
- VPS `.env` 設 `DISABLE_ROBIN=1` 停用 Robin router（PR #13）
- Auth cookie：`nakama_auth`，帶 HttpOnly + Secure + SameSite=Lax（PR #17）
- 詳見 `reference_cloudflare_tunnel.md`

## Franky 工程週報

- backlog：`AgentReports/dev-backlog.md`（Claude Code 維護）
- 週報：`AgentReports/franky/YYYY-WW.md`（每週一 01:00）
- Dashboard 整合：`Dashboards/🏠 Dashboard.md` DataviewJS 區塊

## Tech Stack

- Python 3.12.3, system cron, SQLite
- Claude API (anthropic SDK)
- Syncthing（VPS ↔ Windows ↔ Mac）
- WordPress REST API（Fluent Community + Fluent CRM）
- YouTube Data API v3, Twitter API v2, pytrends, feedparser

**How to apply:** 開發任何 agent 前先讀 `agents/<name>/README.md` 和 `config.yaml`。
