---
name: Nami Calendar × Task 同步 — Phase 2 完成
description: PR #40 merged + VPS deployed + Slack E2E 全通過，Calendar 操作同步建改刪 task
type: project
tags: [nami, calendar, tasks, sync, done]
created: 2026-04-19
updated: 2026-04-19
confidence: high
ttl: 90d
---
## 狀態：DONE（2026-04-19）

**PR #40** squash merged 到 main（commit 3739505），VPS 部署完成並重啟 service，Slack E2E 實測 create/update/delete 全通過。

## 已實作

- `create_calendar_event(also_create_task=true|false)` — 預設同時建 Obsidian Task；撞名 pre-check 擋在 calendar API 之前避免孤兒
- `update_calendar_event` — 依 `calendar_event_id` 找 task，同步 scheduled/scheduled_end/title；rename 時 write-then-delete 避免遺失
- `delete_calendar_event` — 同時刪 linked task；找不到靜默跳過
- Task frontmatter: `calendar_event_id` (str) + `scheduled_end` (ISO, tz-stripped)
- Consistency fixes（code-review 後補）:
  - Rename race: write 先、delete 後
  - Orphan event rollback: task write 失敗時呼叫 `google_calendar.delete_event(event.id)`
- 8 個新測試涵蓋 happy path + also_create_task=false + 撞名 abort + update sync + delete sync + 孤兒 silent + rollback + rename order

## 未來擴充（暫不做）

- 反向同步（Obsidian → Calendar、Calendar UI → Task）
- `push_task_to_calendar` 把既存 task 推到 Calendar
- All-day events 處理
