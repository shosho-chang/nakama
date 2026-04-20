---
name: LifeOS Task frontmatter — 排程用 scheduled key
description: 在 F:/Shosho LifeOS/TaskNotes/Tasks/ 建 Task 檔時的 frontmatter 格式，Nami calendar sync 會抓 scheduled 送 Google Calendar
type: reference
---

## 檔案位置

`F:/Shosho LifeOS/TaskNotes/Tasks/<任務名>.md`

## Frontmatter 格式

```yaml
---
title: <任務名>
status: to-do
priority: normal
tags:
- task
dateCreated: '2026-04-20T10:55:00Z'         # ISO with Z（UTC）
dateModified: '2026-04-20T10:55:00Z'
scheduled: '2026-04-23T09:00:00'            # ISO 無 Z（本地時間）
scheduled_end: '2026-04-23T10:00:00'        # optional，時間段才需要
created: '2026-04-20'                        # 日期 only
updated: '2026-04-20'
projects:                                    # optional，wikilink 陣列
  - "[[肌酸的妙用]]"
---
```

## 注意

- `scheduled` 無 Z（本地時間）— 和 `dateCreated` 的 UTC Z 不同
- 只有 `scheduled` 的 task 會被 Nami calendar sync 抓走送 Google Calendar
- `scheduled_end` 沒設 → calendar 視為 all-day 或單點提醒；有設 → 時間段事件
- Body 可以寫任何 markdown，calendar description 會取前幾行或整段

## 現有範例

- `到知識衛星開會.md` — 單點 scheduled
- `師大圖書館校區講座.md` — scheduled + scheduled_end（會有 `calendar_event_id` 回寫）
- `Robin ingest A B 測試 - Qwen vs Gemma.md` — 另一個 scheduled + scheduled_end 範例（2026-04-20 建）

## 相關

- Nami calendar sync 基礎設施：[project_nami_calendar_task_sync.md](project_nami_calendar_task_sync.md)
- Template drift 警告：[project_lifeos_template_drift.md](project_lifeos_template_drift.md) — gold standard 是 Projects/肌酸的妙用.md，不是 Templates/tpl-*.md
