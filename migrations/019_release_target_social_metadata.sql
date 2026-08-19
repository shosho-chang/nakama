-- 019_release_target_social_metadata.sql
-- Stage 6 multi-platform release target metadata.
--
-- Existing YouTube rows remain valid: all columns are nullable.  The canonical
-- runtime DDL and idempotent upgrade path live in shared/state.py::_init_tables.

ALTER TABLE release_targets ADD COLUMN adapter TEXT;
ALTER TABLE release_targets ADD COLUMN idempotency_key TEXT;
ALTER TABLE release_targets ADD COLUMN checkpoint_json TEXT;
ALTER TABLE release_targets ADD COLUMN ineligibility_reason TEXT;
