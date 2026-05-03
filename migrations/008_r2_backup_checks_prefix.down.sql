-- migrations/008_r2_backup_checks_prefix.down.sql
--
-- Reverse migration 008. SQLite < 3.35 does not support DROP COLUMN; the
-- standard pattern is rebuild-via-temp-table. We assume the runner targets
-- SQLite >= 3.35 (Python 3.11 ships with libsqlite3 >= 3.40 on every modern
-- platform we support).

DROP INDEX IF EXISTS idx_r2_backup_prefix_time;

ALTER TABLE r2_backup_checks DROP COLUMN prefix;
