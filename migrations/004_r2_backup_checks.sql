-- migrations/004_r2_backup_checks.sql
--
-- ADR-007 §5 — Franky daily R2 backup verification records.
--
-- Phase 1 application code creates the table via `shared.state._init_tables`
-- (executescript convention); this .sql file is the canonical DDL reference for
-- a future migration runner (yoyo / alembic).
--
-- Records one row per verification pass. Used by:
--   - agents/franky/r2_backup_verify.py (writes)
--   - agents/franky/weekly_digest.py (reads for 7-day summary — Slice 3)

CREATE TABLE IF NOT EXISTS r2_backup_checks (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    checked_at          TEXT NOT NULL,                          -- ISO 8601 + tz
    latest_object_key   TEXT,
    latest_object_size  INTEGER,
    latest_object_mtime TEXT,                                   -- ISO 8601 + tz (may be Asia/Taipei per ADR-007 §5)
    status              TEXT NOT NULL CHECK (status IN ('ok', 'stale', 'missing', 'too_small')),
    detail              TEXT
);

CREATE INDEX IF NOT EXISTS idx_r2_backup_time
    ON r2_backup_checks(checked_at DESC);
