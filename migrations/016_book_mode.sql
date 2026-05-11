-- Phase 1 monolingual-zh pilot (PRD #507 minimal subset, see
-- docs/plans/2026-05-10-monolingual-zh-pilot-minimal.md).
-- Mirror copy in shared/state.py::_init_tables per project convention.
--
-- ALTER TABLE here covers any deployed prod env where the books row
-- already exists. ``IF NOT EXISTS`` is unsupported on ALTER TABLE ADD
-- COLUMN in SQLite, so the runner must be idempotent at the migration
-- layer (apply once per env). The matching CREATE TABLE in
-- ``_init_tables`` carries the column inline for fresh DBs.

ALTER TABLE books ADD COLUMN mode TEXT NOT NULL DEFAULT 'bilingual-en-zh';
