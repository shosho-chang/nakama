# Capability Card — `nakama-approval-queue`

**Status:** Phase 1 in-development（part of `shared/approval_queue.py` + `thousand_sunny/routers/bridge_drafts.py`）
**License:** MIT（計畫開源）
**Scope:** 通用 Human-in-the-loop approval queue，任何 AI agent 發布前的最後 gate。

---

## 設計前提

- 單人 reviewer（可擴多人，預設一人）
- Web UI + Obsidian vault 雙軌 edit
- SQLite 儲存（單機零依賴）
- Agent-agnostic：source_agent / target_platform 都是 string，不綁定 WordPress

## 能力

| 功能 | 方法 |
|---|---|
| Agent enqueue draft | `queue.enqueue(source_agent, target_platform, action, payload)` |
| Agent poll approved | `queue.peek_approved(source_agent, limit)` |
| Agent mark executed | `queue.mark_executed(draft_id, result)` |
| UI list queue | `GET /bridge/drafts` |
| UI approve | `POST /bridge/drafts/{id}/approve` |
| UI reject with reason | `POST /bridge/drafts/{id}/reject` |
| UI inline edit | `POST /bridge/drafts/{id}/edit` |
| UI export to Obsidian | `POST /bridge/drafts/{id}/export` |
| UI reimport from Obsidian | `POST /bridge/drafts/obsidian-reimport/{id}` |

## 資料模型

詳見 [ADR-006](../decisions/ADR-006-hitl-approval-queue.md) 第 1 節。

## 開源價值

獨立可用於：
- 任何 AI 寫作/發布 agent 的 HITL gate
- Slack / Discord / IG 等 platform 的通用 draft approval
- LLM 生成的 code review 建議 queue

所有 platform-specific 邏輯在 caller agent（如 Usopp），queue 本身不懂「什麼是 post」，只處理 payload dict + UI 渲染。

## 依賴

- Python 3.10+
- `fastapi >= 0.110`
- `sqlite3`（標準庫）
- `jinja2`（UI 渲染）

## 不做的事

- 不做多人投票 approve（第一版單 reviewer）
- 不做細粒度 role-based（未來團隊化再加）
- 不做加密 payload（state.db 加密交給 OS-level 處理）

## 契約測試

- Unit: in-memory SQLite
- Integration: 完整 Bridge flow

## Roadmap

- [ ] v0.1 — Phase 1：queue + 基本 UI + Obsidian export
- [ ] v0.2 — Keyboard shortcuts、diff view 升級
- [ ] v0.3 — Multi-reviewer + webhook 通知第三方 service
