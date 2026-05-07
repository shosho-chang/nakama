-- migrations/014_proposal_metrics.sql
--
-- ADR-022 §6 — proposal_metrics table.
--
-- Purpose: persistence layer for the franky evolution-loop proposal lifecycle
-- (candidate → promoted → triaged → ready|wontfix → shipped → verified|rejected).
-- The retrospective module reads this table to answer "did we actually get
-- stronger?" — without it, retrospective is narrative not feedback.
--
-- Schema is canonical per ADR-022 §6 (18 columns + CHECK constraints + UNIQUE
-- proposal_id). Schema_version=1 is implicit by table identity; future column
-- additions MUST use a `__v2` suffix migration to preserve old-column semantics
-- (see ADR-022 §"Cost guard"). Do NOT change column meanings in place.
--
-- Status FSM SoT (ALLOWED_TRANSITIONS) lives in
-- `agents/franky/state/proposal_metrics.py`. The CHECK enum below is a manual
-- mirror; an import-time assert in that module rejects drift.

CREATE TABLE IF NOT EXISTS proposal_metrics (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id         TEXT NOT NULL UNIQUE,
    issue_number        INTEGER,
    week_iso            TEXT NOT NULL,        -- e.g. '2026-W18'
    related_adr         TEXT,                  -- JSON array
    related_issues      TEXT,                  -- JSON array
    metric_type         TEXT NOT NULL CHECK (metric_type IN ('quantitative','checklist','human_judged')),
    success_metric      TEXT NOT NULL,
    baseline_source     TEXT,
    baseline_value      TEXT,                  -- only for quantitative metrics
    post_ship_value     TEXT,                  -- filled at retrospective time
    verification_owner  TEXT,
    try_cost_estimate   TEXT,
    panel_recommended   INTEGER NOT NULL CHECK (panel_recommended IN (0,1)),
    status              TEXT NOT NULL CHECK (status IN ('candidate','promoted','triaged','ready','wontfix','shipped','verified','rejected')),
    created_at          TEXT NOT NULL,
    promoted_at         TEXT,
    triaged_at          TEXT,
    shipped_at          TEXT,
    verified_at         TEXT,
    related_pr          TEXT,
    related_commit      TEXT,
    source_item_ids     TEXT                   -- JSON array of original news item IDs
);

CREATE INDEX IF NOT EXISTS idx_proposal_metrics_status
    ON proposal_metrics(status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_proposal_metrics_week
    ON proposal_metrics(week_iso, created_at DESC);
