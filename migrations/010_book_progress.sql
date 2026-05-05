-- Slice 3A: per-book reading progress (shared/state.py::_init_tables mirror).
-- FK → books.book_id ON DELETE CASCADE so progress dies with the book.

CREATE TABLE IF NOT EXISTS book_progress (
    book_id               TEXT PRIMARY KEY,
    last_cfi              TEXT,
    last_chapter_ref      TEXT,
    last_spread_idx       INTEGER NOT NULL DEFAULT 0,
    percent               REAL    NOT NULL DEFAULT 0.0,
    total_reading_seconds INTEGER NOT NULL DEFAULT 0,
    updated_at            TEXT    NOT NULL,
    FOREIGN KEY (book_id) REFERENCES books(book_id) ON DELETE CASCADE
);
