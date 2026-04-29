"""Franky cron jobs (slim entrypoints + business logic per task).

Files in this package each own one cron schedule and one job:

    gsc_daily.py — ADR-008 Phase 2a-min: daily 7-day GSC pull → state.db

Existing Franky cron jobs (`news_digest.py`, `r2_backup_verify.py`, etc.) live
directly under `agents/franky/` for historical reasons; new jobs land here so
the directory structure mirrors the cron-tab line-by-line.
"""
