-- migrations/018_strength_sets.sql
--
-- Zoro fitness coach WP1 — per-set strength-training log read back from Garmin
-- (python-garminconnect get_activity_exercise_sets, a bare passthrough endpoint).
--
-- Owner: shared/strength_sets_store.py. Writer: agents/zoro/coach (coach-sync).
-- Readers: WP2 progression engine (volume-load / E1RM / 2-for-2).
--
-- Pattern follows news_score_shadow (migration 015): id PK AUTOINCREMENT, a
-- natural UNIQUE key for idempotent re-sync via INSERT OR IGNORE. Phase 1
-- application code creates this via shared.state._init_tables (executescript
-- convention); this .sql file is the canonical DDL reference (no auto-runner).
--
-- Natural key (activity_id, set_index) where set_index = the raw Garmin
-- `messageIndex` (a stable per-activity ordinal that covers ACTIVE and REST
-- sets uniformly), or the array position when messageIndex is absent (older
-- payloads). Deviates from v2 §4.3's (activity_id, exercise_key, set_index):
-- Phase 0 dumps showed exerciseSets is a flat messageIndex-ordered list and
-- exercise identity is derived, not a key — so messageIndex IS the stable key.
--
-- Phase 0 field findings (real Fenix 8 dump, 4 activities / 55 sets):
--   * weight is GRAMS -> store kg (grams/1000). NULL or 0.0 both mean "no load"
--     (0.0 on warm-up/stretch/bodyweight; NULL when not entered).
--   * reps: NULL on REST sets, 0 on aborted/spurious sets.
--   * category/exercise_name are uppercase FIT enums; name is often NULL.
--   * category WARM_UP / UNKNOWN -> is_warmup flag / excluded by WP2.

CREATE TABLE IF NOT EXISTS strength_sets (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id    TEXT    NOT NULL,
    set_index      INTEGER NOT NULL,   -- raw messageIndex, or array position when absent
    set_type       TEXT    NOT NULL CHECK (set_type IN ('active', 'rest')),
    exercise_key   TEXT,               -- derived: exercise_name or category; NULL on REST
    category       TEXT,               -- raw exercises[0].category (FIT enum)
    exercise_name  TEXT,               -- raw exercises[0].name (FIT enum, often NULL)
    reps           INTEGER,            -- NULL on REST / 0 on aborted set
    weight_kg      REAL,               -- grams/1000; NULL or 0.0 == no load
    duration_sec   REAL,
    is_warmup      INTEGER NOT NULL DEFAULT 0 CHECK (is_warmup IN (0, 1)),
    performed_at   TEXT    NOT NULL,   -- set startTime, ISO +08:00 aware (naive assumed Asia/Taipei)
    wkt_step_index INTEGER,            -- raw wktStepIndex (planned-step link; NULL when freeform)
    source         TEXT    NOT NULL CHECK (source IN ('garmin', 'manual')),
    operation_id   TEXT    NOT NULL,
    created_at     TEXT    NOT NULL,
    UNIQUE (activity_id, set_index)
);

CREATE INDEX IF NOT EXISTS idx_strength_sets_activity
    ON strength_sets(activity_id, set_index);

CREATE INDEX IF NOT EXISTS idx_strength_sets_exercise
    ON strength_sets(exercise_key, performed_at DESC);
