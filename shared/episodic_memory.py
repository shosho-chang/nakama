"""Episodic memory layer — Memory v2 Phase 2a.

Semantic memory (``shared.agent_memory`` / ``user_memories``) holds *stable
beliefs* about the user — one row per subject. Episodic memory holds *timestamped
observations* — "what happened" — many of which share a theme. The reflection
pass (``shared.memory_reflection``) reads recent episodic events and PROMOTES
recurring patterns into semantic facts (e.g. three "週二錄課" observations across
three weeks → a semantic ``工作習慣: 週二固定錄課``). That episodic→semantic
consolidation is the over-time learning the research calls the missing piece
(arxiv 2502.06975).

Deliberately a SEPARATE table (no ``UNIQUE(subject)``) so the populated
``user_memories`` table needs no migration. Un-promoted events age out via
``forget_older_than`` (retention); promoted events are already represented in
semantic memory so they're dropped too.

An episodic row is ACTIVE iff ``invalidated_at IS NULL``. ``promoted_to`` points
at the ``user_memories.id`` it was folded into (NULL = still pending).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from shared.log import get_logger
from shared.state import _get_conn

logger = get_logger("nakama.episodic_memory")

_SCHEMA_INITIALIZED = False


def _ensure_schema() -> None:
    global _SCHEMA_INITIALIZED
    if _SCHEMA_INITIALIZED:
        return
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS episodic_memories (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            agent          TEXT NOT NULL,
            user_id        TEXT NOT NULL,
            observation    TEXT NOT NULL,
            occurred_at    TEXT NOT NULL,
            source_thread  TEXT,
            created_at     TEXT NOT NULL,
            promoted_to    INTEGER,
            invalidated_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_episodic_lookup
            ON episodic_memories(agent, user_id, occurred_at DESC);
    """)
    conn.commit()
    _SCHEMA_INITIALIZED = True


@dataclass
class EpisodicMemory:
    id: int
    agent: str
    user_id: str
    observation: str
    occurred_at: str
    source_thread: str | None
    created_at: str
    promoted_to: int | None
    invalidated_at: str | None


def _row_to_episodic(row: sqlite3.Row) -> EpisodicMemory:
    return EpisodicMemory(
        id=row["id"],
        agent=row["agent"],
        user_id=row["user_id"],
        observation=row["observation"],
        occurred_at=row["occurred_at"],
        source_thread=row["source_thread"],
        created_at=row["created_at"],
        promoted_to=row["promoted_to"],
        invalidated_at=row["invalidated_at"],
    )


def add_episodic(
    agent: str,
    user_id: str,
    observation: str,
    *,
    occurred_at: str | None = None,
    source_thread: str | None = None,
) -> int:
    """Record one timestamped observation. ``occurred_at`` defaults to now (UTC).

    No dedup — episodic events are append-only by nature; consolidation happens
    later in the reflection pass. Returns the new row id.
    """
    _ensure_schema()
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO episodic_memories
              (agent, user_id, observation, occurred_at, source_thread, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (agent, user_id, observation, occurred_at or now, source_thread, now),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_recent(
    agent: str,
    user_id: str,
    *,
    days: int = 30,
    limit: int = 100,
    include_promoted: bool = False,
) -> list[EpisodicMemory]:
    """Active episodic events for (agent, user) within the last ``days``, newest
    first. By default excludes already-promoted events (the reflection pass wants
    only the unconsolidated backlog)."""
    _ensure_schema()
    conn = _get_conn()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conditions = ["agent = ?", "user_id = ?", "invalidated_at IS NULL", "occurred_at >= ?"]
    params: list = [agent, user_id, cutoff]
    if not include_promoted:
        conditions.append("promoted_to IS NULL")
    where = " AND ".join(conditions)
    rows = conn.execute(
        f"""SELECT id, agent, user_id, observation, occurred_at, source_thread,
                   created_at, promoted_to, invalidated_at
            FROM episodic_memories
            WHERE {where}
            ORDER BY occurred_at DESC
            LIMIT ?""",
        [*params, limit],
    ).fetchall()
    return [_row_to_episodic(r) for r in rows]


def mark_promoted(ids: list[int], semantic_id: int) -> int:
    """Mark episodic rows as folded into ``user_memories[semantic_id]``. They stop
    showing in the unconsolidated backlog. Returns rows affected."""
    if not ids:
        return 0
    _ensure_schema()
    conn = _get_conn()
    placeholders = ",".join("?" * len(ids))
    cur = conn.execute(
        f"UPDATE episodic_memories SET promoted_to = ? WHERE id IN ({placeholders})",
        [semantic_id, *ids],
    )
    conn.commit()
    return cur.rowcount


def forget_older_than(*, days: int = 60) -> int:
    """Soft-invalidate active episodic events older than ``days`` (retention).

    Episodic memory is a short-horizon backlog: anything worth keeping has been
    promoted to semantic by then; the raw event log shouldn't grow forever.
    Returns rows affected.
    """
    _ensure_schema()
    conn = _get_conn()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "UPDATE episodic_memories SET invalidated_at = ? "
        "WHERE invalidated_at IS NULL AND occurred_at < ?",
        (now, cutoff),
    )
    conn.commit()
    return cur.rowcount
