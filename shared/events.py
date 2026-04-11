"""Agent 間事件匯流排（Event Bus）。

用法：
    # 發送事件
    from shared.events import emit
    emit("zoro", "intel_ready", {"topics": ["longevity", "NAD+"], "report_path": "..."})

    # 消費事件（同一個 target 只會收到未被消費的事件）
    from shared.events import consume
    events = consume(target="robin", event_type="intel_ready")
    for ev in events:
        print(ev["source"], ev["payload"])
"""

import json
from datetime import datetime, timezone
from typing import Any

from shared.state import _get_conn


def emit(source: str, event_type: str, payload: dict[str, Any] | None = None) -> int:
    """發送事件到 Event Bus。

    Args:
        source:     發送事件的 agent 名稱（如 "zoro"）
        event_type: 事件類型（如 "intel_ready"、"content_ready"）
        payload:    事件資料，任意 dict

    Returns:
        事件的 id
    """
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO agent_events (source, event_type, payload, created_at) VALUES (?, ?, ?, ?)",
        (source, event_type, json.dumps(payload or {}, ensure_ascii=False), now),
    )
    conn.commit()
    return cur.lastrowid


def consume(target: str, event_type: str) -> list[dict[str, Any]]:
    """取得並標記消費事件。

    同一個 (target, event_type) 組合只會收到尚未被該 target 消費的事件。

    Args:
        target:     消費事件的 agent 名稱（如 "robin"）
        event_type: 事件類型過濾（如 "intel_ready"）

    Returns:
        事件列表，每筆包含 id, source, event_type, payload(dict), created_at
    """
    conn = _get_conn()
    rows = conn.execute(
        """SELECT id, source, event_type, payload, created_at
           FROM agent_events
           WHERE event_type = ?
             AND (consumed_by IS NULL OR consumed_by != ?)
           ORDER BY created_at ASC""",
        (event_type, target),
    ).fetchall()

    if not rows:
        return []

    now = datetime.now(timezone.utc).isoformat()
    ids = [row["id"] for row in rows]
    conn.execute(
        "UPDATE agent_events SET consumed_by = ?, consumed_at = ?"
        f" WHERE id IN ({','.join('?' * len(ids))})",
        [target, now, *ids],
    )
    conn.commit()

    return [
        {
            "id": row["id"],
            "source": row["source"],
            "event_type": row["event_type"],
            "payload": json.loads(row["payload"]),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def peek(event_type: str, limit: int = 20) -> list[dict[str, Any]]:
    """查看 Event Bus 中的事件（不標記消費，用於 debug）。

    Args:
        event_type: 事件類型過濾（空字串表示全部）
        limit:      最多回傳幾筆

    Returns:
        事件列表（含消費狀態）
    """
    conn = _get_conn()
    if event_type:
        rows = conn.execute(
            "SELECT * FROM agent_events WHERE event_type = ? ORDER BY created_at DESC LIMIT ?",
            (event_type, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM agent_events ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    return [
        {
            "id": row["id"],
            "source": row["source"],
            "event_type": row["event_type"],
            "payload": json.loads(row["payload"]),
            "created_at": row["created_at"],
            "consumed_by": row["consumed_by"],
            "consumed_at": row["consumed_at"],
        }
        for row in rows
    ]
