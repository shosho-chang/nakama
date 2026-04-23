-- migrations/002_publish_jobs.sql
--
-- ADR-005b §1 / §2 — Usopp publish_jobs state machine.
--
-- Phase 1 application code creates this via `shared.state._init_tables` (executescript
-- convention inherited from approval_queue / memories). This .sql file is the canonical
-- DDL reference and is tracked here so a future migration runner (yoyo / alembic) can
-- pick it up without us re-deriving the schema.
--
-- Schema source:
--   - publish_jobs → ADR-005b §1 state machine, §2 idempotency, §10 compliance flags

CREATE TABLE IF NOT EXISTS publish_jobs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Source linkage
    draft_id            TEXT    NOT NULL UNIQUE,              -- DraftV1.draft_id ("draft_YYYYMMDDTHHMMSS_abcdef")
    approval_queue_id   INTEGER NOT NULL,                     -- FK approval_queue.id (logical, not enforced)
    operation_id        TEXT    NOT NULL,                     -- observability correlation (matches DraftV1)

    -- State machine (ADR-005b §1)
    state               TEXT    NOT NULL
                        CHECK (state IN ('claimed', 'media_ready', 'post_draft',
                                         'seo_ready', 'validated', 'published',
                                         'cache_purged', 'done', 'failed')),
    state_updated_at    TEXT    NOT NULL,                     -- ISO 8601 + tz

    -- Progressive outputs (filled as we advance)
    featured_media_id   INTEGER,                              -- set after upload_media (optional step)
    post_id             INTEGER,                              -- set after create_post(status=draft)
    permalink           TEXT,                                 -- set after status=publish
    seo_status          TEXT
                        CHECK (seo_status IS NULL OR
                               seo_status IN ('written', 'fallback_meta', 'skipped')),
    cache_purged        INTEGER NOT NULL DEFAULT 0,           -- 0/1 boolean

    -- Compliance (ADR-005b §10; JSON of PublishComplianceGateV1)
    compliance_flags    TEXT,

    -- Timing
    claimed_at          TEXT    NOT NULL,                     -- when Usopp grabbed from approval_queue
    completed_at        TEXT,                                 -- set when state reaches done or failed

    -- Failure tracking
    failure_reason      TEXT,
    retry_count         INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_publish_jobs_state
    ON publish_jobs(state, state_updated_at);

CREATE INDEX IF NOT EXISTS idx_publish_jobs_approval_queue
    ON publish_jobs(approval_queue_id);

CREATE INDEX IF NOT EXISTS idx_publish_jobs_operation
    ON publish_jobs(operation_id);
