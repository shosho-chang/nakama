"""User-scoped agent memory store (Semantic 層).

與 ``shared.memory`` / ``shared.state.memories`` 不同 —— 那是 agent 在執行任務時
「自己學到的知識」（ADR-002 Tier 3）。這個模組是 agent 對**使用者**的記憶：
偏好、決策、重要事實，跨對話、跨 thread 存活。

Schema（table ``user_memories``）：
    (agent, user_id, subject) 組合唯一 → upsert 語意
    confidence: 0-1 float，可透過 decay 降低
    last_accessed_at: search 命中時更新，用於排序

典型用法：
    add(agent="nami", user_id="U1", type="preference",
        subject="工作時段", content="修修習慣早上做深度工作")

    memories = search(agent="nami", user_id="U1", limit=10)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from shared.state import _get_conn

_SCHEMA_INITIALIZED = False


def _ensure_schema() -> None:
    global _SCHEMA_INITIALIZED
    if _SCHEMA_INITIALIZED:
        return
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS user_memories (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            agent             TEXT NOT NULL,
            user_id           TEXT NOT NULL,
            type              TEXT NOT NULL,
            subject           TEXT NOT NULL,
            content           TEXT NOT NULL,
            confidence        REAL NOT NULL DEFAULT 1.0,
            source_thread     TEXT,
            created_at        TEXT NOT NULL,
            last_accessed_at  TEXT NOT NULL,
            UNIQUE(agent, user_id, subject)
        );

        CREATE INDEX IF NOT EXISTS idx_user_memories_lookup
            ON user_memories(agent, user_id);
    """)
    conn.commit()
    _SCHEMA_INITIALIZED = True


@dataclass
class UserMemory:
    """從 DB 取出的記憶物件。"""

    id: int
    agent: str
    user_id: str
    type: str
    subject: str
    content: str
    confidence: float
    source_thread: str | None
    created_at: str
    last_accessed_at: str


def _row_to_memory(row: sqlite3.Row) -> UserMemory:
    return UserMemory(
        id=row["id"],
        agent=row["agent"],
        user_id=row["user_id"],
        type=row["type"],
        subject=row["subject"],
        content=row["content"],
        confidence=row["confidence"],
        source_thread=row["source_thread"],
        created_at=row["created_at"],
        last_accessed_at=row["last_accessed_at"],
    )


def add(
    agent: str,
    user_id: str,
    type: str,
    subject: str,
    content: str,
    *,
    confidence: float = 1.0,
    source_thread: str | None = None,
) -> int:
    """新增或覆寫記憶。(agent, user_id, subject) 命中則 update content+confidence。

    回傳記憶的 ``id``。
    """
    _ensure_schema()
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        """INSERT INTO user_memories
              (agent, user_id, type, subject, content, confidence, source_thread,
               created_at, last_accessed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(agent, user_id, subject) DO UPDATE SET
              type = excluded.type,
              content = excluded.content,
              confidence = excluded.confidence,
              source_thread = COALESCE(excluded.source_thread, user_memories.source_thread),
              last_accessed_at = excluded.last_accessed_at""",
        (agent, user_id, type, subject, content, confidence, source_thread, now, now),
    )
    conn.commit()

    row = conn.execute(
        "SELECT id FROM user_memories WHERE agent = ? AND user_id = ? AND subject = ?",
        (agent, user_id, subject),
    ).fetchone()
    return int(row["id"])


def search(
    agent: str,
    user_id: str,
    *,
    query: str | None = None,
    type: str | None = None,
    limit: int = 20,
) -> list[UserMemory]:
    """按 confidence × recency 排序回傳記憶。

    - ``query``：若給，對 subject/content 做 LIKE 關鍵字匹配
    - ``type``：若給，過濾類型
    - 命中的記憶 ``last_accessed_at`` 會被更新
    """
    _ensure_schema()
    conn = _get_conn()

    conditions = ["agent = ?", "user_id = ?"]
    params: list = [agent, user_id]

    if type:
        conditions.append("type = ?")
        params.append(type)
    if query:
        conditions.append("(subject LIKE ? OR content LIKE ?)")
        like = f"%{query}%"
        params.extend([like, like])

    where = " AND ".join(conditions)
    # Ranking: confidence * (1 / (1 + hours_since_access))
    # SQLite 算 julianday 差值：(julianday('now') - julianday(last_accessed_at)) * 24 = hours
    order_expr = (
        "confidence * (1.0 / (1.0 + (julianday('now') - julianday(last_accessed_at)) * 24))"
    )

    rows = conn.execute(
        f"""SELECT id, agent, user_id, type, subject, content, confidence,
                   source_thread, created_at, last_accessed_at
            FROM user_memories
            WHERE {where}
            ORDER BY {order_expr} DESC
            LIMIT ?""",
        [*params, limit],
    ).fetchall()

    memories = [_row_to_memory(r) for r in rows]

    if memories:
        now = datetime.now(timezone.utc).isoformat()
        ids = [m.id for m in memories]
        placeholders = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE user_memories SET last_accessed_at = ? WHERE id IN ({placeholders})",
            [now, *ids],
        )
        conn.commit()

    return memories


def list_all(agent: str, user_id: str, *, limit: int = 50) -> list[UserMemory]:
    """列出該 user + agent 的所有記憶（不更新 last_accessed_at，給 Bridge UI 用）。"""
    _ensure_schema()
    conn = _get_conn()
    rows = conn.execute(
        """SELECT id, agent, user_id, type, subject, content, confidence,
                  source_thread, created_at, last_accessed_at
           FROM user_memories
           WHERE agent = ? AND user_id = ?
           ORDER BY created_at DESC
           LIMIT ?""",
        (agent, user_id, limit),
    ).fetchall()
    return [_row_to_memory(r) for r in rows]


def forget(memory_id: int) -> bool:
    """刪除一筆記憶，回傳是否實際刪到。"""
    _ensure_schema()
    conn = _get_conn()
    cur = conn.execute("DELETE FROM user_memories WHERE id = ?", (memory_id,))
    conn.commit()
    return cur.rowcount > 0


def decay(*, older_than_days: int = 30, factor: float = 0.9) -> int:
    """把超過 ``older_than_days`` 沒被存取的記憶 confidence * factor。

    回傳受影響筆數。用於定期維護（例如每週跑一次）。
    """
    _ensure_schema()
    conn = _get_conn()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
    cur = conn.execute(
        "UPDATE user_memories SET confidence = confidence * ? WHERE last_accessed_at < ?",
        (factor, cutoff),
    )
    conn.commit()
    return cur.rowcount


def list_subjects(agent: str, user_id: str) -> list[str]:
    """回傳該 user + agent 所有現存 subject（供抽取器重用，避免語意重複）。"""
    _ensure_schema()
    conn = _get_conn()
    rows = conn.execute(
        "SELECT subject FROM user_memories WHERE agent = ? AND user_id = ? ORDER BY subject",
        (agent, user_id),
    ).fetchall()
    return [r["subject"] for r in rows]


def list_subjects_with_content(agent: str, user_id: str) -> list[tuple[str, str]]:
    """回傳該 user + agent 所有 (subject, content) pair，供抽取器 merge 用。"""
    _ensure_schema()
    conn = _get_conn()
    rows = conn.execute(
        """SELECT subject, content FROM user_memories
           WHERE agent = ? AND user_id = ? ORDER BY subject""",
        (agent, user_id),
    ).fetchall()
    return [(r["subject"], r["content"]) for r in rows]


def format_as_context(agent: str, user_id: str, *, limit: int = 20) -> str:
    """把該 user + agent 的 top-N 記憶組成供 LLM 注入的 context block。

    搜尋時會更新 ``last_accessed_at``，記憶越常被注入代表越活躍。
    沒有記憶時回傳空字串（呼叫端應略過注入）。
    """
    memories = search(agent=agent, user_id=user_id, limit=limit)
    if not memories:
        return ""

    lines = ["## 你記得關於使用者的事"]
    for m in memories:
        lines.append(f"- [{m.type}] {m.subject}：{m.content}")
    return "\n".join(lines)


def prune(*, confidence_threshold: float = 0.1) -> int:
    """刪除 confidence 低於 threshold 的記憶。回傳刪除筆數。"""
    _ensure_schema()
    conn = _get_conn()
    cur = conn.execute(
        "DELETE FROM user_memories WHERE confidence < ?",
        (confidence_threshold,),
    )
    conn.commit()
    return cur.rowcount
