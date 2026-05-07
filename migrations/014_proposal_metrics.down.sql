-- migrations/014_proposal_metrics.down.sql
-- Rollback for 014_proposal_metrics.sql — drops the proposal lifecycle table.

DROP INDEX IF EXISTS idx_proposal_metrics_week;
DROP INDEX IF EXISTS idx_proposal_metrics_status;
DROP TABLE IF EXISTS proposal_metrics;
