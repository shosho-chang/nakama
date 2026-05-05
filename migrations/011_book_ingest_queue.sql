-- Slice 4A: ingest queue for background textbook ingestion.
-- FK → books.book_id ON DELETE CASCADE so queue entries die with the book.
-- PK on book_id means at most one active queue row per book.

CREATE TABLE IF NOT EXISTS book_ingest_queue (
    book_id        TEXT PRIMARY KEY,
    status         TEXT NOT NULL DEFAULT 'queued'
                   CHECK (status IN ('queued','ingesting','ingested','partial','failed')),
    requested_at   TEXT NOT NULL,
    started_at     TEXT,
    completed_at   TEXT,
    chapters_done  INTEGER NOT NULL DEFAULT 0,
    error          TEXT,
    FOREIGN KEY (book_id) REFERENCES books(book_id) ON DELETE CASCADE
);
