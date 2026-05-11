-- migrations/008_r2_backup_checks_prefix.sql
--
-- Add `prefix` column to `r2_backup_checks` so per-prefix verify runs do not
-- cross-pollute consecutive-fail counting and AlertV1 dedup_key.
--
-- Motivation: Franky's xcloud-backup bucket holds both `shosho/...` (daily
-- fresh from main site backup) and `fleet/...` (newly added 2026-05-03).
-- A single verify_once over the entire bucket masks fleet staleness — the
-- shosho daily fresh object always wins `max(objects, key=last_modified)`
-- and status flips to 'ok' even when fleet/ has not been written for days.
--
-- Forward migration: ALTER TABLE ADD COLUMN with NOT NULL DEFAULT ''.
-- Existing rows backfill to '' (semantically: "verified the entire bucket").
-- Application code idempotently re-applies via try/except (see shared/state.py).
--
-- Touched by:
--   - agents/franky/r2_backup_verify.py (writes prefix; queries per-prefix history)
--   - agents/franky/__main__.py        (loops verify_all_prefixes from env CSV)

ALTER TABLE r2_backup_checks
    ADD COLUMN prefix TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_r2_backup_prefix_time
    ON r2_backup_checks(prefix, checked_at DESC);
