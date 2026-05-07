-- ADR-023 §7 S2b: 5-dim shadow scoring table.
-- Records both 4-dim (v1, legacy pick gate) and 5-dim (v2, shadow record)
-- overalls per scored item during the 1-week shadow period.
-- Shadow mode ends when human review confirms weights → relevance ≥ 3 gate activated.
CREATE TABLE IF NOT EXISTS news_score_shadow (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id    TEXT    NOT NULL,
    item_id         TEXT    NOT NULL,
    scored_at       TEXT    NOT NULL,
    -- raw 5-dim scores (1–5 each)
    signal          REAL    NOT NULL,
    novelty         REAL    NOT NULL,
    actionability   REAL    NOT NULL,
    noise           REAL    NOT NULL,
    relevance       REAL    NOT NULL,
    -- computed overalls
    overall_v1      REAL    NOT NULL,   -- 4-dim: (s×1.5 + n×1.0 + a×1.2 + q×1.0) / 4.7
    overall_v2      REAL    NOT NULL,   -- 5-dim: (s×1.5 + n×1.0 + a×1.2 + q×1.0 + r×1.3) / 6.0
    -- shadow pick gate outcome
    pick_shadow     INTEGER NOT NULL CHECK (pick_shadow IN (0, 1)),
    -- ADR-N or #issue cited when relevance ≥ 3 (nullable)
    relevance_ref   TEXT,
    UNIQUE (operation_id, item_id)
);

CREATE INDEX IF NOT EXISTS idx_news_score_shadow_op
    ON news_score_shadow(operation_id, scored_at DESC);

CREATE INDEX IF NOT EXISTS idx_news_score_shadow_item
    ON news_score_shadow(item_id, scored_at DESC);
