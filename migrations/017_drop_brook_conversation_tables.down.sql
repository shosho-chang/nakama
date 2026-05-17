-- migrations/017_drop_brook_conversation_tables.down.sql
--
-- Rollback for 017_drop_brook_conversation_tables.sql — re-creates the
-- empty Brook conversational tables.
--
-- Note: the rollback intentionally re-creates EMPTY tables. Data dropped
-- by 017 is unrecoverable; this down-migration restores schema only so the
-- old compose.py code path can be reactivated for ad-hoc debugging.
-- Production code (PR-3 + PR-4) does not depend on these tables.

CREATE TABLE IF NOT EXISTS brook_conversations (
    id          TEXT PRIMARY KEY,
    topic       TEXT NOT NULL,
    phase       TEXT NOT NULL DEFAULT 'outline',
    kb_context  TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS brook_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES brook_conversations(id)
);

CREATE INDEX IF NOT EXISTS idx_brook_messages_conv
    ON brook_messages(conversation_id, id);
