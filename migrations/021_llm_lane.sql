-- ADR-070 D5（S2a，issue #1321）：訂閱額度用完的狀態機，單列全域狀態。
-- Canonical mirror: shared/state.py（_init_tables 會自動建立，這支只做文件鏡像）。
CREATE TABLE IF NOT EXISTS llm_lane_state (
    id                          INTEGER PRIMARY KEY CHECK (id = 1),
    version                     INTEGER NOT NULL DEFAULT 0,
    updated_at                  TEXT NOT NULL,
    interactive_status          TEXT NOT NULL DEFAULT 'subscription',
    interactive_blocked_family  TEXT,
    interactive_rate_limit_type TEXT,
    interactive_resets_at       INTEGER,
    interactive_spend_usd       REAL NOT NULL DEFAULT 0,
    interactive_spend_day       TEXT,
    interactive_switched_at     TEXT,
    batch_status                TEXT NOT NULL DEFAULT 'subscription',
    batch_blocked_family        TEXT,
    batch_rate_limit_type       TEXT,
    batch_resets_at             INTEGER,
    batch_spend_usd             REAL NOT NULL DEFAULT 0,
    batch_cap_usd               REAL,
    batch_switched_at           TEXT
);
