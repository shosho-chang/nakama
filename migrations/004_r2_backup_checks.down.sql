-- migrations/004_r2_backup_checks.down.sql
-- Rollback for 004_r2_backup_checks.sql — drops daily verification history.

DROP INDEX IF EXISTS idx_r2_backup_time;
DROP TABLE IF EXISTS r2_backup_checks;
