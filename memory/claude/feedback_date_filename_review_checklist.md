---
name: Any new daily-rotating filename or key must use ZoneInfo("Asia/Taipei"), not UTC
description: PR #67 precedent keeps getting re-stepped — make it a hard review gate for every new date-partitioned path
type: feedback
tags: [timezone, date, filename, review-checklist, vps]
---
Every new daily-rotating filename, log rotation suffix, R2 key, or SQL snapshot name must anchor its date to `datetime.now(ZoneInfo("Asia/Taipei"))` — never `date.today()` / `datetime.utcnow()` / `datetime.now(timezone.utc)`. Applies to **new** date-based paths specifically; timestamps used for comparison logic (retention cutoffs, created_at columns) stay UTC-native.

**Why:** PR #67 (Robin PubMed digest filename) fixed this class of bug once. Memory `reference_vps_timezone.md` records the invariant. Despite both, **PR #88 (Nakama self-backup, 2026-04-24)** re-stepped it exactly: `datetime.now(timezone.utc)` used in R2 key path `state/YYYY/MM/DD/state.db.gz`, while cron fires at 04:00 Asia/Taipei (= 20:00 UTC previous day), so backups would land in yesterday's folder from operator perspective. Caught by local code-review skill (score 95) before merge, fixed in follow-up commit + regression test. The lesson: the invariant is written down but isn't reliably surfaced at write time — needs to be a review checklist item, not just a retrospective memory.

**How to apply:**
- When writing any new daily-rotating path (filename / R2 key / log suffix / tarball name): reach for `ZoneInfo("Asia/Taipei")` on the same keystroke.
- When reviewing a PR that adds a `%Y/%m/%d` or `%Y%m%d` formatting: grep for `now` / `today` in the same file; confirm it's Taipei-anchored.
- Write a regression test that freezes the clock at **04:00 Asia/Taipei across the UTC day boundary** (= 20:00 UTC previous day). This is the only test setup that fails for UTC-coded paths; noon-UTC tests pass silently.
- Retention / cutoff comparisons against boto3 `LastModified` should stay UTC — the math is correct and the concern is only operator-perceived calendar dates, not machine arithmetic.
