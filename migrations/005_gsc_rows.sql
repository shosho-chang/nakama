-- migrations/005_gsc_rows.sql
--
-- ADR-008 §2 — Phase 2a-min `gsc_rows` table.
--
-- Persists daily GSC search-analytics rows (one per query × page × country ×
-- device per day). Daily cron `python -m agents.franky gsc-daily` pulls a
-- 7-day window ending `today - 4` (Asia/Taipei, GSC 2-4d delay per ADR-008 §2)
-- and UPSERTs into this table. Overlap with prior days re-writes (idempotent).
--
-- Phase 2a-min scope (this migration): just the `gsc_rows` table + indexes.
-- Phase 2a-full follow-up (separate PR): alert rules + weekly digest section.
--
-- Schema source: ADR-008 §2 (`docs/decisions/ADR-008-seo-observability.md`).
-- Pydantic shape: `shared/schemas/seo.py` `GSCRowV1`.
-- Application code creates this via `shared.state._init_tables` (executescript
-- convention inherited from approval_queue / memories / r2_backup_checks).
-- This .sql file is the canonical DDL reference for a future migration runner.
--
-- Note: file numbered 005 because 003_franky_tables.sql + 004_r2_backup_checks.sql
-- are already taken; ADR-008 §2 originally said 003 but the slot pre-existed.

CREATE TABLE IF NOT EXISTS gsc_rows (
    site         TEXT NOT NULL,                     -- 'sc-domain:shosho.tw' format
    date         TEXT NOT NULL,                     -- ISO 'YYYY-MM-DD' (GSC reports per-day)
    query        TEXT NOT NULL,                     -- search query string (max 200 chars)
    page         TEXT NOT NULL,                     -- landing URL (max 2000 chars)
    country      TEXT NOT NULL,                     -- ISO 3166-1 alpha-2/3 (e.g. 'twn', 'usa')
    device       TEXT NOT NULL                      -- GSC device dimension
                 CHECK (device IN ('desktop', 'mobile', 'tablet')),
    clicks       INTEGER NOT NULL,
    impressions  INTEGER NOT NULL,
    ctr          REAL    NOT NULL,                  -- [0.0, 1.0]
    position     REAL    NOT NULL,                  -- average position, ≥ 1.0
    fetched_at   TEXT    NOT NULL,                  -- ISO 8601 + tz, when this row was upserted
    PRIMARY KEY (site, date, query, page, country, device)
);

CREATE INDEX IF NOT EXISTS idx_gsc_site_date
    ON gsc_rows(site, date DESC);

CREATE INDEX IF NOT EXISTS idx_gsc_query
    ON gsc_rows(query);
