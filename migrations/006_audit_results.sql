-- migrations/006_audit_results.sql
--
-- PRD #226 §"Audit result schema" — SEO 中控台 v1 audit_results table.
--
-- Persists one row per audit run. Multiple audits per (target_site, wp_post_id)
-- are stored as a history (PRIMARY KEY id AUTOINCREMENT); `latest_for_post` is
-- a query-time aggregate, not a DB-level constraint. This lets us keep audit
-- history (User Story #4 in PRD #226) without per-row de-dup pain.
--
-- Phase 1 application code creates this via `shared.state._init_tables`
-- (executescript convention inherited from approval_queue / memories /
-- gsc_rows). This .sql file is the canonical DDL reference for a future
-- migration runner.
--
-- Schema source: PRD #226 §"Audit result schema" + grilling decision Q8.
-- Pydantic shape: `shared/schemas/seo_audit_review.py` `AuditSuggestionV1`.
-- Owner: `shared/audit_results_store.py`. Writers: `agents/brook/audit_runner`.
-- Readers: `thousand_sunny/routers/bridge.py` (/bridge/seo + /bridge/seo/audits).
--
-- Why id PRIMARY KEY (not (target_site, wp_post_id)): the PRD asks for
-- "PK (target_site, wp_post_id)" as an *intent* — one canonical row per
-- post. But User Story #4 requires audit history (past audits with grades +
-- dates), and external audits can run with `wp_post_id=NULL` (URL-only). A
-- composite PK would forbid both. Resolution: AUTOINCREMENT id PK + secondary
-- index on (target_site, wp_post_id, audited_at DESC) so `latest_for_post`
-- is a fast lookup, while history queries stay cheap. Slice 5 (#234) review
-- session writes update the same row in place via `update_suggestion`.
--
-- Note: file numbered 006 because 003-005 are already taken.
-- The SEO 中控台 PRD originally said "004_audit_results.sql"; that slot was
-- claimed by `004_r2_backup_checks.sql` (Franky Slice 2) before this PR.

CREATE TABLE IF NOT EXISTS audit_results (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,

    -- target_site = `wp_shosho` / `wp_fleet` (Literal in `shared/schemas/publishing.py`).
    -- NULL allowed for non-WP / external audits — those use `url` for lookup.
    target_site       TEXT,

    -- WP post id when the audit was kicked off from `/bridge/seo` section 1.
    -- NULL for external / non-WP audits (URL only).
    wp_post_id        INTEGER,

    -- Canonical URL the audit ran against. Always present.
    url               TEXT NOT NULL,

    -- Focus keyword (from SEOPress meta or user input). Empty string when unset.
    focus_keyword     TEXT NOT NULL DEFAULT '',

    -- ISO 8601 + tz, when the audit subprocess finished and rows were written.
    audited_at        TEXT NOT NULL,

    -- A / B+ / B / C+ / C / D / F (per `audit.py::_grade`).
    overall_grade     TEXT NOT NULL CHECK (overall_grade IN ('A','B+','B','C+','C','D','F')),

    -- Counts of pass/warn/fail/skip checks across all rules (deterministic +
    -- LLM). Used for the result page header + section 1 summary chip.
    pass_count        INTEGER NOT NULL DEFAULT 0,
    warn_count        INTEGER NOT NULL DEFAULT 0,
    fail_count        INTEGER NOT NULL DEFAULT 0,
    skip_count        INTEGER NOT NULL DEFAULT 0,

    -- list[AuditSuggestionV1] as JSON. Each element has rule_id / severity /
    -- title / current_value / suggested_value / rationale / status /
    -- edited_value / reviewed_at. Slice #5 review writes update items in place.
    suggestions_json  TEXT NOT NULL DEFAULT '[]',

    -- Raw markdown audit report (the `audit.py` output written to a tmp file
    -- then read back). Stored verbatim so the result page can render it
    -- without re-running the audit.
    raw_markdown      TEXT NOT NULL DEFAULT '',

    -- Review FSM. PRD §"Review semantics":
    --   fresh      = audit just finished, no review yet
    --   in_review  = user opened review page (slice #234)
    --   exported   = at least 1 suggestion approved/edited and exported into
    --                approval_queue (slice #235)
    --   archived   = manually filed away
    review_status     TEXT NOT NULL DEFAULT 'fresh'
                      CHECK (review_status IN ('fresh','in_review','exported','archived')),

    -- Set when slice #235 export creates an approval_queue row.
    approval_queue_id INTEGER
);

-- Latest-for-post lookup: section 1 join + `update_suggestion` resolution.
-- DESC on audited_at so `LIMIT 1` returns the newest row first.
CREATE INDEX IF NOT EXISTS idx_audit_results_post_audited_at
    ON audit_results(target_site, wp_post_id, audited_at DESC);

-- URL secondary index for non-WP / external audits where `wp_post_id IS NULL`.
CREATE INDEX IF NOT EXISTS idx_audit_results_url
    ON audit_results(url, audited_at DESC);

-- Used by future "fresh audits to review" surfaces (slice #234 inbox).
CREATE INDEX IF NOT EXISTS idx_audit_results_review_status
    ON audit_results(review_status, audited_at DESC);
