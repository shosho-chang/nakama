"""Slack thread-scoped conversation state for multi-turn handlers.

When a handler returns a `HandlerResponse` with a `continuation` dict, the bot
registers that flow against the reply's thread. Subsequent messages posted
in the same thread are routed back to the same handler's `continue_flow()`
with the stored state dict, until the handler returns a response without a
`continuation` (which ends the flow) or the idle timeout expires.

Storage is SQLite-backed (`conversations.db`) so conversation state survives a
gateway restart / deploy / VPS reboot — the in-memory dict it replaced silently
dropped every in-flight thread on restart (and on the 30-min timeout), which
made Nami re-enter a fresh, context-less `handle()` mid-task and hallucinate.

Path resolution mirrors `shared.log_index.from_default_path`:
  1. `NAKAMA_CONVERSATIONS_DB_PATH` env override (full path) — tests
  2. `NAKAMA_DATA_DIR` env (data dir, file appended) — VPS sets this
  3. `<repo_root>/data/conversations.db` — local dev fallback
Tests pass `db_path=":memory:"` for an isolated, throwaway store.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

# Idle window before a conversation is evicted. Was 1800 (30 min) — far too short
# for human Slack pacing (a 47-min gap between turns silently dropped context and
# Nami restarted fresh). 24h matches how a single user actually chats across a day.
DEFAULT_TIMEOUT_SECONDS = 86400  # 24 hours


@dataclass
class Conversation:
    thread_ts: str
    channel: str
    user_id: str
    agent_name: str
    flow_name: str
    state: dict = field(default_factory=dict)
    last_activity: float = field(default_factory=time.time)


def _resolve_db_path() -> str:
    override = os.environ.get("NAKAMA_CONVERSATIONS_DB_PATH")
    if override:
        return override
    data_dir = os.environ.get("NAKAMA_DATA_DIR")
    if data_dir:
        return str(Path(data_dir) / "conversations.db")
    repo_root = Path(__file__).resolve().parent.parent
    return str(repo_root / "data" / "conversations.db")


class ConversationStore:
    """SQLite-backed thread→conversation store. Same API as the old in-memory
    impl; one connection per store (``check_same_thread=False`` since the gateway
    serves all three bots from one process across Slack-SDK threads)."""

    def __init__(
        self,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        *,
        db_path: str | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._lock = threading.Lock()
        self._db_path = db_path or _resolve_db_path()
        if self._db_path != ":memory:":
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS conversations(
                thread_ts      TEXT PRIMARY KEY,
                channel        TEXT NOT NULL,
                user_id        TEXT NOT NULL,
                agent_name     TEXT NOT NULL,
                flow_name      TEXT NOT NULL,
                state_json     TEXT NOT NULL DEFAULT '{}',
                last_activity  REAL NOT NULL
            )"""
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_conv_user_agent "
            "ON conversations(user_id, agent_name, last_activity DESC)"
        )
        self._conn.commit()

    @staticmethod
    def _row_to_conv(row: sqlite3.Row) -> Conversation:
        return Conversation(
            thread_ts=row["thread_ts"],
            channel=row["channel"],
            user_id=row["user_id"],
            agent_name=row["agent_name"],
            flow_name=row["flow_name"],
            state=json.loads(row["state_json"]) if row["state_json"] else {},
            last_activity=row["last_activity"],
        )

    def start(
        self,
        *,
        thread_ts: str,
        channel: str,
        user_id: str,
        agent_name: str,
        flow_name: str,
        state: dict | None = None,
    ) -> Conversation:
        conv = Conversation(
            thread_ts=thread_ts,
            channel=channel,
            user_id=user_id,
            agent_name=agent_name,
            flow_name=flow_name,
            state=state or {},
        )
        with self._lock:
            self._conn.execute(
                """INSERT INTO conversations
                     (thread_ts, channel, user_id, agent_name, flow_name,
                      state_json, last_activity)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(thread_ts) DO UPDATE SET
                     channel=excluded.channel,
                     user_id=excluded.user_id,
                     agent_name=excluded.agent_name,
                     flow_name=excluded.flow_name,
                     state_json=excluded.state_json,
                     last_activity=excluded.last_activity""",
                (
                    thread_ts,
                    channel,
                    user_id,
                    agent_name,
                    flow_name,
                    json.dumps(conv.state, ensure_ascii=False),
                    conv.last_activity,
                ),
            )
            self._conn.commit()
        return conv

    def get(self, thread_ts: str) -> Conversation | None:
        with self._lock:
            self._evict_expired_locked()
            row = self._conn.execute(
                "SELECT * FROM conversations WHERE thread_ts = ?", (thread_ts,)
            ).fetchone()
            return self._row_to_conv(row) if row else None

    def update(self, thread_ts: str, state: dict) -> None:
        with self._lock:
            # No-op if the thread is gone (matches old dict behavior).
            self._conn.execute(
                "UPDATE conversations SET state_json = ?, last_activity = ? WHERE thread_ts = ?",
                (json.dumps(state, ensure_ascii=False), time.time(), thread_ts),
            )
            self._conn.commit()

    def end(self, thread_ts: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM conversations WHERE thread_ts = ?", (thread_ts,))
            self._conn.commit()

    def active_count(self) -> int:
        with self._lock:
            self._evict_expired_locked()
            row = self._conn.execute("SELECT COUNT(*) AS n FROM conversations").fetchone()
            return int(row["n"])

    def get_latest_for_user_and_agent(self, user_id: str, agent_name: str) -> Conversation | None:
        """找特定 user + agent 的最新活躍 conversation（DM 中 thread_ts 丟失時用）。"""
        with self._lock:
            self._evict_expired_locked()
            row = self._conn.execute(
                """SELECT * FROM conversations
                   WHERE user_id = ? AND agent_name = ?
                   ORDER BY last_activity DESC LIMIT 1""",
                (user_id, agent_name),
            ).fetchone()
            return self._row_to_conv(row) if row else None

    def _evict_expired_locked(self) -> None:
        cutoff = time.time() - self._timeout
        self._conn.execute("DELETE FROM conversations WHERE last_activity < ?", (cutoff,))
        self._conn.commit()


_STORE: ConversationStore | None = None


def get_store() -> ConversationStore:
    # Lazy so importing the module doesn't create data/conversations.db (keeps
    # test collection / CI from leaving a stray file in the repo).
    global _STORE
    if _STORE is None:
        _STORE = ConversationStore()
    return _STORE
