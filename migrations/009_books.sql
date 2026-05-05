-- Slice 1C: bilingual EPUB book library (shared/book_storage.py).
-- Mirror copy in shared/state.py::_init_tables per project convention.

CREATE TABLE IF NOT EXISTS books (
    book_id           TEXT PRIMARY KEY,
    title             TEXT NOT NULL,
    author            TEXT,
    lang_pair         TEXT NOT NULL,
    genre             TEXT,
    isbn              TEXT,
    published_year    INTEGER,
    has_original      INTEGER NOT NULL DEFAULT 0,
    book_version_hash TEXT NOT NULL,
    created_at        TEXT NOT NULL
);
