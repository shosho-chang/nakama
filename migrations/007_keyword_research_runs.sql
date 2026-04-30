-- migrations/007_keyword_research_runs.sql
--
-- PRD #255 §"Schema" — keyword_research_runs table for Slice 2 (#258 / A′).
--
-- Persists one row per keyword-research run kicked off via the web UI
-- (`/bridge/zoro/keyword-research`). The LifeOS dataviewjs path stays
-- vault-only and does NOT write here this round (per PRD Out of Scope).
--
-- Pattern follows `audit_results` (migration 006): id PK AUTOINCREMENT so
-- each run is its own row and history queries are cheap. Phase 1 application
-- code creates this via `shared.state._init_tables` (executescript convention
-- inherited from approval_queue / memories / gsc_rows / audit_results). This
-- .sql file is the canonical DDL reference for a future migration runner.
--
-- Owner: `shared/keyword_research_history_store.py`. Writers: web POST
-- handler in `thousand_sunny/routers/bridge_zoro.py`. Readers: list / detail
-- pages under the same router (`/bridge/zoro/keyword-research/history`).

CREATE TABLE IF NOT EXISTS keyword_research_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Topic the user typed in the form. NOT NULL because the router rejects
    -- whitespace-only topics with HTTP 400 before reaching here (so this
    -- never sees an empty string in practice).
    topic        TEXT NOT NULL,

    -- Optional english equivalent the user typed (or NULL if Claude
    -- auto-translated). Stored as-typed; no normalization.
    en_topic     TEXT,

    -- Form select: 'blog' or 'youtube' (changes Claude's title-suggestion
    -- prompt downstream, but doesn't affect storage shape).
    content_type TEXT NOT NULL CHECK (content_type IN ('blog', 'youtube')),

    -- Full markdown report including frontmatter, exactly as
    -- `agents.zoro.report_renderer.render_markdown` returned. Round-tripped
    -- through this column for the detail-page re-download path.
    report_md    TEXT NOT NULL,

    -- ISO 8601 + tz-aware UTC timestamp (matching audit_results.audited_at
    -- convention). Display layer converts to Asia/Taipei.
    created_at   TEXT NOT NULL,

    -- 'web'   — POST /bridge/zoro/keyword-research succeeded and persisted
    -- 'lifeos' — reserved for future (LifeOS dataviewjs path that may
    --            optionally backfill; not used by Slice 2)
    triggered_by TEXT NOT NULL CHECK (triggered_by IN ('web', 'lifeos'))
);

-- List page index: chronological scan + pagination.
CREATE INDEX IF NOT EXISTS idx_keyword_research_created_at
    ON keyword_research_runs(created_at DESC);

-- Topic-grouped lookup ("show me all my 間歇性斷食 runs").
CREATE INDEX IF NOT EXISTS idx_keyword_research_topic
    ON keyword_research_runs(topic, created_at DESC);
