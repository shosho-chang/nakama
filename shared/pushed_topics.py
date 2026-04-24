"""Agent-internal log of topics an agent已pushed to internal discussion.

Zoro 使用情境（brainstorm scout）：
- Scout 每次選題後把 (topic, normalized_keywords, pushed_at) 記下來
- 下次選題前用 `is_novel()` 查 KB 14 天內是否有近似題
- 用 `is_on_cooldown()` 查 48h 內是否有近似題（更嚴閾值）

設計為 agent-generic — 之後其他 agent 要記「推過什麼主題給內部討論」也能用。
Schema 與 `user_memories`（user-scoped）分開，因為這是 agent 自己的內部日誌，
跟 user 無關。

判斷「近似」：normalized keywords 集合的 Jaccard similarity。
"""

from __future__ import annotations

import re
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
        CREATE TABLE IF NOT EXISTS pushed_topics (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            agent                 TEXT NOT NULL,
            topic                 TEXT NOT NULL,
            normalized_keywords   TEXT NOT NULL,
            pushed_at             TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_pushed_topics_agent_time
            ON pushed_topics(agent, pushed_at);
    """)
    conn.commit()
    _SCHEMA_INITIALIZED = True


@dataclass(frozen=True)
class PushedTopic:
    id: int
    agent: str
    topic: str
    normalized_keywords: frozenset[str]
    pushed_at: datetime


def normalize_keywords(words: list[str]) -> list[str]:
    """統一化關鍵詞供 Jaccard 比較 — lower + 去標點 + 去空字串 + unique。"""
    out: list[str] = []
    seen: set[str] = set()
    for w in words:
        w2 = re.sub(r"[^\w\s-]", "", w.strip().lower())
        w2 = re.sub(r"\s+", " ", w2).strip()
        if not w2 or w2 in seen:
            continue
        seen.add(w2)
        out.append(w2)
    return out


def jaccard(a: set[str] | frozenset[str], b: set[str] | frozenset[str]) -> float:
    """集合 Jaccard 相似度。空集合回 0（不是 1）— 不要把空題當成跟任何東西近似。"""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def record(agent: str, topic: str, normalized_keywords: list[str]) -> int:
    """把一筆 pushed_topic 寫入 DB。回傳新 row id。"""
    _ensure_schema()
    conn = _get_conn()
    kws = normalize_keywords(normalized_keywords)
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO pushed_topics (agent, topic, normalized_keywords, pushed_at) "
        "VALUES (?, ?, ?, ?)",
        (agent, topic, " ".join(kws), now),
    )
    conn.commit()
    return cur.lastrowid or 0


def _row_to_topic(row: sqlite3.Row) -> PushedTopic:
    kws = (
        frozenset(row["normalized_keywords"].split()) if row["normalized_keywords"] else frozenset()
    )
    return PushedTopic(
        id=row["id"],
        agent=row["agent"],
        topic=row["topic"],
        normalized_keywords=kws,
        pushed_at=datetime.fromisoformat(row["pushed_at"]),
    )


def recent(agent: str, *, since: timedelta) -> list[PushedTopic]:
    """回傳該 agent 在 `since` 內 push 過的 topics，新到舊。"""
    _ensure_schema()
    conn = _get_conn()
    cutoff = (datetime.now(timezone.utc) - since).isoformat()
    rows = conn.execute(
        "SELECT * FROM pushed_topics WHERE agent = ? AND pushed_at >= ? ORDER BY pushed_at DESC",
        (agent, cutoff),
    ).fetchall()
    return [_row_to_topic(r) for r in rows]


def is_novel(
    agent: str,
    candidate_keywords: list[str],
    *,
    days: int = 14,
    threshold: float = 0.6,
) -> bool:
    """候選題跟過去 `days` 天內任何 push 過的 topic 都不相似（Jaccard < threshold）→ novel。

    預設 14 天 / 0.6：較長窗期但較寬鬆閾值，避免重複處理近期討論過的大方向。
    """
    kws = frozenset(normalize_keywords(candidate_keywords))
    if not kws:
        return False
    history = recent(agent, since=timedelta(days=days))
    for h in history:
        if jaccard(kws, h.normalized_keywords) >= threshold:
            return False
    return True


def is_on_cooldown(
    agent: str,
    candidate_keywords: list[str],
    *,
    hours: int = 48,
    threshold: float = 0.3,
) -> bool:
    """候選題跟過去 `hours` 小時內 push 過的任一題相似（Jaccard ≥ threshold）→ cooldown。

    預設 48h / 0.3：較短窗期但較嚴閾值 — 防止一天內連推兩個氣味相近的題。
    """
    kws = frozenset(normalize_keywords(candidate_keywords))
    if not kws:
        return False
    history = recent(agent, since=timedelta(hours=hours))
    for h in history:
        if jaccard(kws, h.normalized_keywords) >= threshold:
            return True
    return False


def delete_for_agent(agent: str) -> int:
    """測試用：清空某 agent 的所有紀錄。回傳刪除筆數。"""
    _ensure_schema()
    conn = _get_conn()
    cur = conn.execute("DELETE FROM pushed_topics WHERE agent = ?", (agent,))
    conn.commit()
    return cur.rowcount
