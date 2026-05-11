-- Migration 012: KB hybrid retrieval tables
--
-- Three tables for the Phase 1a hybrid retrieval engine (issue #431):
--   kb_chunks       — FTS5 full-text index (BM25 lane)
--   kb_vectors      — vec0 dense-vector index (semantic lane); requires sqlite-vec
--   kb_index_meta   — incremental-index bookkeeping (mtime_ns short-circuit)
--
-- These tables live in kb_index.db (separate from state.db) because sqlite-vec
-- must be loaded as an extension, which is incompatible with the shared state.db
-- connection that intentionally does NOT load extensions.
-- Schema is initialized by shared/kb_hybrid_search._init_schema(); this file
-- is the canonical DDL reference.

CREATE TABLE IF NOT EXISTS kb_index_meta (
    path       TEXT PRIMARY KEY,
    mtime_ns   INTEGER NOT NULL,
    file_hash  TEXT    NOT NULL,
    indexed_at TEXT    NOT NULL
);

CREATE VIRTUAL TABLE kb_chunks USING fts5(
    chunk_text,         -- body of the H2 section; BM25 weight 1.0
    section,            -- H2 heading text (e.g. "定義"); BM25 weight 0.5
    heading_context,    -- page title from frontmatter; BM25 weight 0.3
    path UNINDEXED,     -- e.g. "KB/Wiki/Concepts/overtraining"
    tokenize='porter unicode61'
);

-- vec0 requires sqlite-vec extension; dim=256 matches model2vec potion-base-8M
CREATE VIRTUAL TABLE kb_vectors USING vec0(
    embedding float[256]
);
