-- migrations/017_drop_brook_conversation_tables.sql
--
-- ADR-027 §Decision 8 (PR-3): Drop the SQLite tables that backed the
-- conversational `/brook/chat` flow. The route is now a stateless
-- "context bridge" (see thousand_sunny/routers/brook.py + agents/brook/
-- context_bridge.py) — Claude.ai owns chat state, Nakama only packages
-- context.
--
-- These tables were originally created at runtime by
-- agents/brook/compose.py::_init_brook_tables() rather than via a numbered
-- migration; this is the first time they appear in the migrations stream.
-- Dropping is safe: no code path remaining in PR-3 reads or writes them.
-- The compose.py module itself is killed in PR-4 (issue #586).
--
-- WARNING: This permanently deletes any persisted chat history. The
-- approval_queue (Brook drafts pending review) is NOT affected — those
-- live in `approval_queue` and stay intact.

DROP INDEX IF EXISTS idx_brook_messages_conv;
DROP TABLE IF EXISTS brook_messages;
DROP TABLE IF EXISTS brook_conversations;
