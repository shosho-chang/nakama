-- migrations/019_cardio_sessions.sql
--
-- Zoro fitness coach — summary-level cardio sessions (running / cycling /
-- swimming) read back from Garmin's activity list (get_activities_by_date).
-- Unlike strength, cardio needs no per-set detail — the list call already
-- carries distance / duration / speed / HR / calories.
--
-- Owner: shared/cardio_sessions_store.py. Writer: agents/zoro/coach (coach-sync).
-- Reader: the 有氧一覽 panel on /bridge/weekly. Pattern mirrors strength_sets
-- (migration 018): id PK + a natural UNIQUE key for idempotent re-sync.
-- Phase 1 app code creates this via shared.state._init_tables; this .sql is the
-- canonical DDL reference.
--
-- Field findings (real Fenix 8 dump): distance in METRES, duration in SECONDS,
-- averageSpeed in m/s, HR as float (stored INTEGER), startTimeLocal is naive
-- local ("YYYY-MM-DD HH:MM:SS") -> stored ISO +08:00.

CREATE TABLE IF NOT EXISTS cardio_sessions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id    TEXT    NOT NULL,
    activity_type  TEXT    NOT NULL,   -- running / cycling / lap_swimming / ...
    performed_at   TEXT    NOT NULL,   -- ISO +08:00 (naive local assumed Asia/Taipei)
    duration_sec   REAL,
    distance_m     REAL,
    avg_speed_mps  REAL,
    avg_hr         INTEGER,
    max_hr         INTEGER,
    calories       INTEGER,
    source         TEXT    NOT NULL CHECK (source IN ('garmin', 'manual')),
    operation_id   TEXT    NOT NULL,
    created_at     TEXT    NOT NULL,
    UNIQUE (activity_id)
);

CREATE INDEX IF NOT EXISTS idx_cardio_sessions_time
    ON cardio_sessions(performed_at DESC);

CREATE INDEX IF NOT EXISTS idx_cardio_sessions_type
    ON cardio_sessions(activity_type, performed_at DESC);
