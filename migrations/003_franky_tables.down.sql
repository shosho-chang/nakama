-- migrations/003_franky_tables.down.sql
--
-- Rollback for 003_franky_tables.sql.
-- WARNING: drops alert dedup state and health probe counters. Re-applying 003 will
-- recreate empty tables; in-flight alerts may re-fire once until dedup state rebuilds.

DROP INDEX IF EXISTS idx_alert_state_suppress;
DROP INDEX IF EXISTS idx_alert_state_rule;
DROP TABLE IF EXISTS alert_state;

DROP INDEX IF EXISTS idx_health_probe_status;
DROP TABLE IF EXISTS health_probe_state;
