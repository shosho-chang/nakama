-- S4: ground truth 👍/👎 signals from digest.md checkboxes (issue #434).
-- Dual-use: future Chopper retrieval QA dataset.
-- Unique on (book_id, item_cfi, hit_path) — idempotent upsert.
CREATE TABLE IF NOT EXISTS kb_search_feedback (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id    TEXT    NOT NULL,
    item_cfi   TEXT    NOT NULL,
    query_text TEXT    NOT NULL DEFAULT '',
    hit_path   TEXT    NOT NULL,
    signal     TEXT    NOT NULL CHECK (signal IN ('up', 'down')),
    marked_at  TEXT    NOT NULL,
    source     TEXT    NOT NULL DEFAULT 'digest',
    UNIQUE (book_id, item_cfi, hit_path)
);

CREATE INDEX IF NOT EXISTS idx_kb_search_feedback_book
    ON kb_search_feedback(book_id, marked_at DESC);
