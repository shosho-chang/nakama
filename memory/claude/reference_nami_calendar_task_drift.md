---
name: reference-nami-calendar-task-drift
description: Nami 的任務↔Google Calendar 只靠 calendar_event_id 單鍵匹配、找不到就靜默 no-op，會讓任務永久脫鉤；已有偵測網 (#968) + 根因修復 (#969) + 除錯指令
metadata:
  type: reference
---

Nami（`gateway/handlers/nami.py`）把 TaskNotes 任務跟 Google Calendar 事件**只用 `calendar_event_id` 這一個鍵連結**。`_sync_task_from_calendar_update` 在找不到帶該 id 的任務時**直接 `return ""`（靜默 no-op）**。因此一旦某任務沒帶 event_id（例如當初是 `create_task` / Obsidian 建的裸 `scheduled`，或建事件時標題跟任務 slug 不符），之後每一次 `update_calendar_event` 都會跳過它 → **永久脫鉤且沒人發現**；`create_calendar_event` 也可能留下孤兒事件（事件建了、任務沒建成）。Nami 的 Slack 回報是「我打算做的」不是「實際 vault 狀態」，所以會顯示全 ✅ 但實際壞掉。

**症狀（2026-07-01 知識衛星 正課/募資 reschedule 事件）：** 有些任務建成有些沒（7/10 正課事件在、任務沒建）；有些連到 Google Cal 有些沒（7/24、7/31 只有裸 `scheduled`、無 plan、無 event_id）。

**Why:** 單鍵匹配 + 靜默 no-op = drift 一旦發生就無聲累積。這類錯誤在 2026 上半年反覆出現。

**How to apply（除錯 / 防護）:**
- 遇到「任務跟行事曆對不上」先跑權威稽核，**不要信 Nami 的 Slack 摘要**：
  `python -m agents.franky calendar-reconcile --dry-run`（只稽核不寫）。它列出 unlinked / no_event / time_mismatch / dangling / orphan_event 五類 drift。
- 引擎是 `shared/calendar_reconcile.py` `sweep()`（以 **(title, date)** 配對，自動連結 unambiguous 的 unlinked，其餘只回報）。既有的 `reconcile_scheduled_tasks`（Codex）只認 `{slug}@{date}` idempotency key，**事件標題≠任務 slug 就抓不到**，別只靠它。
- 每日偵測網：Franky cron `python -m agents.franky calendar-reconcile`（PR #968）—— deploy 後要在 VPS crontab 手動加一行才會跑；有 drift 才 Slack 通知修修。
- 根因修復（PR #969）：`_sync_task_from_calendar_update` 找不到 id 連結時會退回 `_find_task_by_title` + 依日期就地補配對，不再靜默跳過。
- 手動修個別脫鉤：連到既有事件用 `weekly_writer.upsert_plan_entry(..., calendar_event_id=<既有 id>)`（**不會**新建 Google 事件）；裸 `scheduled` 要順手 `fm.pop("scheduled")`。1🍅 = 30 分，plan 番茄數 = 事件時長 ÷ 30。
