---
name: Nami Calendar × Task 同步設計（PRD approved）
description: Nami 操作 Calendar 時同步建/改/刪 Obsidian Task；PRD 已 approve，待 Phase 2/3
type: project
tags: [nami, calendar, tasks, sync, next-session]
created: 2026-04-19
updated: 2026-04-19
confidence: high
ttl: 60d
originSessionId: 6dc774b2-8ee7-4655-b691-eebe19832245
---
## 背景

Google Calendar 整合 PR #39 已 merge + VPS 實測通過，但 Calendar 和 Obsidian Task 兩個系統各自獨立 — Slack 排「明天下午 3 點讀書」後 Obsidian Tasks view 看不到。下一步做**單向同步**（Nami 的 Calendar 操作 → Task 跟著動）。

## Scope（已 approve）

**包含：**
- `create_calendar_event` → 同時建 Task 檔，frontmatter 多 `calendar_event_id` + `scheduled_end`
- `update_calendar_event` → 依 `calendar_event_id` 找 task，更新 `scheduled` / `scheduled_end` / `title`
- `delete_calendar_event` → 依 `calendar_event_id` 找 task 刪除
- Task frontmatter 新增 `calendar_event_id` (str) + `scheduled_end` (ISO datetime)
- `also_create_task: bool` 參數給 `create_calendar_event`（預設 **true**），LLM 可在「純 event 不需要 task」情境（婚禮、生日）關掉

**排除（明確不做）：**
- ❌ 反向同步：手動改 Obsidian task scheduled → 不會同步到 Calendar（需 file watcher）
- ❌ 反向同步：手動改 Google Calendar UI → 不會同步到 Task（需 webhook）
- ❌ 既存 task「推到 Calendar」（未來可能加 `push_task_to_calendar`，此輪不做）
- ❌ Calendar all-day events（只處理有時間的事件）

## 決策（已 approve）

1. **預設行為**：`also_create_task=true`，LLM 可覆寫
2. **Task priority 預設**：`normal`（Calendar 無 priority 概念）
3. **Task status 預設**：`to-do`（對齊現有 create_task）
4. **Scheduled 格式轉換**：寫入 task 時**剝掉時區**（`2026-04-25T15:00:00+08:00` → `2026-04-25T15:00:00`），對齊現有 task format，避免重寫 Obsidian query

## Edge case 處理（已 approve）

- Task title 撞名 → 照 `ProjectExistsError` 報錯，Nami 回訊息請使用者換標題
- Delete calendar event 時對應 task 已不在 → 靜默跳過（不視為錯誤）
- Update calendar event 改時段，但對應 task 已 `done` → 照樣更新（使用者決定要不要改回）

## 時程初估

8-12h：shared helper（2-3h）+ 3 tool executor 改寫（3-4h）+ 測試（2-3h）+ VPS 部署 + E2E 實測（1-2h）

## 下個工作階段進入點

直接進 **Phase 2 技術設計** — 主要設計點：
- 新 helper function 在哪（`shared/lifeos_writer.py` 加 `create_task_with_calendar_link` / `find_task_by_calendar_id` / `update_task_by_calendar_id` / `delete_task_by_calendar_id`？還是放 `gateway/handlers/nami.py` private method？）
- Calendar event ID 是 Google-side unique string，直接存 task frontmatter，不需 index
- `_find_task_by_calendar_id` 掃 TaskNotes/Tasks/ 讀所有 frontmatter 找 `calendar_event_id == target`（線性掃，跟 `_find_task_by_title` 同等級複雜度）
- 當前 `create_calendar_event` 呼叫 `google_calendar.create_event()` 拿 `CalendarEvent`，有 id → 用這個 id 回寫 task

## 環境狀態

- VPS commit: c1d5b6d（PR #39 已 merge）
- VPS Google Calendar API 已通，token refresh + persist 實戰驗證通過
- SSH alias `nakama-vps` 已設（見 `reference_vps_ssh.md`）
