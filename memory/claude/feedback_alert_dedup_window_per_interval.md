---
name: Alert dedup_window 必對齊 expected interval
description: dedup_window_seconds 不該用全域 default；要按 alert 觸發的 expected frequency 配對，否則 daily failure 變 96 alerts/day
type: feedback
created: 2026-04-26
originSessionId: 74954c3c-fdc6-4064-931b-fe3250a7d804
---
對「sustained-state」類 alert（例：cron stale、bucket empty、disk full），`dedup_window_seconds` 不該用全域 default（nakama 預設 15 min），要按該 alert 對應的 expected interval 配對；否則一個 daily-cycle failure 會在 15 min default 下 fire 96 次/day。

**Why:** PR #170 Phase 5B-1 cron staleness probe 一開始沒設 `dedup_window_seconds`，套 `DEFAULT_DEDUP_WINDOW_S = 15 * 60`。sub-agent reviewer 算出：daily backup 若連 fail，AlertV1 → Slack DM 會每 15 min 發一次，1 天炸 96 條。修法：`dedup_window_s = min(interval_min * 60, 24 * 3600)` — daily backup → 1 alert/24h，weekly job → cap 24h（不要長到忘了）。

**How to apply:**
- 寫新 sustained-state probe 時，dedup_window 必對齊「這條 alert 多久 fire 一次合理」
- daily-cycle → 24h；hourly → 1h；continuous probe → keep default 15min（transient 才適合 default）
- cap 上限 24h：避免 weekly/monthly 條件 dedupe 一週後悄悄 noise，operator 看 `/bridge/franky` chip 仍要看到 still-firing
- 同類風險：cost anomaly、latency anomaly、cron-stale、heartbeat-missing 全部適用
- Reviewer 檢查項：grep AlertV1 構造看有沒有顯式 `dedup_window_seconds=...`，沒設且非 transient 就是 smell
