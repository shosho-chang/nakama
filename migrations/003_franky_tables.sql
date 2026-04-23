-- migrations/003_franky_tables.sql
--
-- ADR-007 §4 / §5 — Franky Phase 1 monitoring tables.
--
-- Phase 1 application code creates these via `shared.state._init_tables` (executescript
-- convention inherited from approval_queue / memories). This .sql file is the canonical
-- DDL reference and is tracked here so a future migration runner (yoyo / alembic) can
-- pick it up without us re-deriving the schema.
--
-- Schema sources:
--   - alert_state         → ADR-007 §4 Alert dedup
--   - health_probe_state  → ADR-007 §4 N-fail counter (decoupled from alert_router)
--
-- Out of scope for Slice 1 (deferred to Slice 2/3):
--   - cron_runs           → ADR-007 §5 (covered when shared/cron_wrapper lands)
--   - vps_metrics         → ADR-007 §5 (covered when vps_monitor.py lands)
--   - r2_backup_checks    → ADR-007 §5 (Slice 2 with r2_backup_verify.py)

-- ---------------------------------------------------------------------------
-- 1. Alert dedup state (ADR-007 §4)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS alert_state (
    dedup_key       TEXT PRIMARY KEY,
    rule_id         TEXT NOT NULL,
    last_fired_at   TEXT NOT NULL,                           -- ISO 8601 + tz
    suppress_until  TEXT NOT NULL,                           -- ISO 8601 + tz
    state           TEXT NOT NULL CHECK (state IN ('firing', 'resolved')),
    last_message    TEXT NOT NULL,
    fire_count      INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_alert_state_suppress
    ON alert_state(suppress_until);

CREATE INDEX IF NOT EXISTS idx_alert_state_rule
    ON alert_state(rule_id, last_fired_at DESC);


-- ---------------------------------------------------------------------------
-- 2. Health probe N-fail counter (ADR-007 §4)
-- ---------------------------------------------------------------------------
-- Decoupled from alert_router: probe accumulates consecutive_fails until it
-- crosses the per-target threshold (default 3), then emits one AlertV1 event.
-- This keeps alert_router stateless and easy to test.

CREATE TABLE IF NOT EXISTS health_probe_state (
    target              TEXT PRIMARY KEY,                    -- e.g. 'wp_shosho', 'wp_fleet', 'nakama_gateway', 'vps_resources'
    consecutive_fails   INTEGER NOT NULL DEFAULT 0,
    last_check_at       TEXT NOT NULL,                       -- ISO 8601 + tz
    last_status         TEXT NOT NULL CHECK (last_status IN ('ok', 'fail')),
    last_error          TEXT
);

CREATE INDEX IF NOT EXISTS idx_health_probe_status
    ON health_probe_state(last_status, last_check_at DESC);
