"""Slack thread-scoped conversation state for multi-turn handlers.

When a handler returns a `HandlerResponse` with a `continuation` dict, the bot
registers that flow against the reply's thread. Subsequent messages posted
in the same thread are routed back to the same handler's `continue_flow()`
with the stored state dict, until the handler returns a response without a
`continuation` (which ends the flow) or the 30-minute timeout expires.

MVP storage is in-memory (thread-safe dict); state is lost on bot restart.
Upgrade path: swap `ConversationStore` for a Redis/SQLite-backed impl when
more handlers need this (e.g. morning-brief).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

DEFAULT_TIMEOUT_SECONDS = 1800  # 30 minutes


@dataclass
class Conversation:
    thread_ts: str
    channel: str
    user_id: str
    agent_name: str
    flow_name: str
    state: dict = field(default_factory=dict)
    last_activity: float = field(default_factory=time.time)


class ConversationStore:
    def __init__(self, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._by_thread: dict[str, Conversation] = {}
        self._timeout = timeout_seconds
        self._lock = threading.Lock()

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
            self._by_thread[thread_ts] = conv
        return conv

    def get(self, thread_ts: str) -> Conversation | None:
        with self._lock:
            self._evict_expired_locked()
            return self._by_thread.get(thread_ts)

    def update(self, thread_ts: str, state: dict) -> None:
        with self._lock:
            conv = self._by_thread.get(thread_ts)
            if conv is None:
                return
            conv.state = state
            conv.last_activity = time.time()

    def end(self, thread_ts: str) -> None:
        with self._lock:
            self._by_thread.pop(thread_ts, None)

    def active_count(self) -> int:
        with self._lock:
            self._evict_expired_locked()
            return len(self._by_thread)

    def _evict_expired_locked(self) -> None:
        now = time.time()
        expired = [ts for ts, c in self._by_thread.items() if now - c.last_activity > self._timeout]
        for ts in expired:
            del self._by_thread[ts]


_STORE = ConversationStore()


def get_store() -> ConversationStore:
    return _STORE
