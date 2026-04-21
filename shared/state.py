"""SQLite 狀態管理：追蹤已處理檔案、agent 執行紀錄、記憶搜尋。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from shared.config import get_db_path

_conn: Optional[sqlite3.Connection] = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        db_path = get_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(db_path), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _init_tables(_conn)
    return _conn


def _init_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS files_processed (
            file_path   TEXT PRIMARY KEY,
            file_hash   TEXT NOT NULL,
            agent       TEXT NOT NULL,
            processed_at TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'done'
        );

        CREATE TABLE IF NOT EXISTS agent_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            agent       TEXT NOT NULL,
            started_at  TEXT NOT NULL,
            finished_at TEXT,
            status      TEXT NOT NULL DEFAULT 'running',
            summary     TEXT,
            input_tokens  INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS api_calls (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            agent               TEXT NOT NULL,
            run_id              INTEGER,
            model               TEXT NOT NULL,
            input_tokens        INTEGER NOT NULL,
            output_tokens       INTEGER NOT NULL,
            cache_read_tokens   INTEGER NOT NULL DEFAULT 0,
            cache_write_tokens  INTEGER NOT NULL DEFAULT 0,
            called_at           TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_api_calls_agent_time
            ON api_calls(agent, called_at);
        CREATE INDEX IF NOT EXISTS idx_api_calls_time
            ON api_calls(called_at);

        CREATE TABLE IF NOT EXISTS scout_seen (
            source      TEXT NOT NULL,
            item_id     TEXT NOT NULL,
            url         TEXT,
            first_seen  TEXT NOT NULL,
            PRIMARY KEY (source, item_id)
        );

        CREATE TABLE IF NOT EXISTS community_alerts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id     TEXT,
            alert_type  TEXT NOT NULL,
            severity    TEXT NOT NULL DEFAULT 'info',
            created_at  TEXT NOT NULL,
            resolved    INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS files_read (
            file_path   TEXT NOT NULL PRIMARY KEY,
            read_at     TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS agent_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source      TEXT NOT NULL,
            event_type  TEXT NOT NULL,
            payload     TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT NOT NULL,
            consumed_by TEXT,
            consumed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS event_consumptions (
            event_id    INTEGER NOT NULL,
            consumer    TEXT NOT NULL,
            consumed_at TEXT NOT NULL,
            PRIMARY KEY (event_id, consumer),
            FOREIGN KEY (event_id) REFERENCES agent_events(id)
        );

        -- ADR-002 Tier 3: 記憶搜尋層
        CREATE TABLE IF NOT EXISTS memories (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            agent       TEXT NOT NULL,
            type        TEXT NOT NULL,
            title       TEXT NOT NULL,
            content     TEXT NOT NULL,
            tags        TEXT NOT NULL DEFAULT '[]',
            confidence  TEXT NOT NULL DEFAULT 'medium',
            source      TEXT,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            expires_at  TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_memories_agent ON memories(agent);
        CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type);
    """)

    # Migration: api_calls 曾經沒有 cache token 欄位（Phase 4 前）。
    # ALTER TABLE ADD COLUMN 沒有 IF NOT EXISTS 語法，用 try/except 補。
    for col_ddl in (
        "ALTER TABLE api_calls ADD COLUMN cache_read_tokens INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE api_calls ADD COLUMN cache_write_tokens INTEGER NOT NULL DEFAULT 0",
    ):
        try:
            conn.execute(col_ddl)
        except sqlite3.OperationalError:
            pass  # 欄位已存在

    # FTS5 虛擬表需要獨立建立（不支援 IF NOT EXISTS 語法）
    try:
        conn.execute("""
            CREATE VIRTUAL TABLE memories_fts USING fts5(
                title, content, tags,
                content='memories',
                content_rowid='id'
            )
        """)
    except sqlite3.OperationalError:
        pass  # 已存在

    # FTS5 同步觸發器
    for trigger_sql in [
        """CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
            INSERT INTO memories_fts(rowid, title, content, tags)
            VALUES (new.id, new.title, new.content, new.tags);
        END""",
        """CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, title, content, tags)
            VALUES ('delete', old.id, old.title, old.content, old.tags);
        END""",
        """CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, title, content, tags)
            VALUES ('delete', old.id, old.title, old.content, old.tags);
            INSERT INTO memories_fts(rowid, title, content, tags)
            VALUES (new.id, new.title, new.content, new.tags);
        END""",
    ]:
        try:
            conn.execute(trigger_sql)
        except sqlite3.OperationalError:
            pass  # 已存在

    conn.commit()


def file_hash(path: Path) -> str:
    """計算檔案的 SHA-256 hash。"""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def is_file_processed(path: Path, agent: str) -> bool:
    """檢查檔案是否已被指定 agent 處理過（且內容未變）。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT file_hash FROM files_processed WHERE file_path = ? AND agent = ?",
        (str(path), agent),
    ).fetchone()
    if row is None:
        return False
    return row["file_hash"] == file_hash(path)


def mark_file_processed(path: Path, agent: str) -> None:
    """標記檔案已處理。"""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT OR REPLACE INTO files_processed
           (file_path, file_hash, agent, processed_at, status)
           VALUES (?, ?, ?, ?, 'done')""",
        (str(path), file_hash(path), agent, now),
    )
    conn.commit()


def is_file_read(path: Path) -> bool:
    """檢查檔案是否已被標記為已閱讀。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT 1 FROM files_read WHERE file_path = ?",
        (str(path),),
    ).fetchone()
    return row is not None


def mark_file_read(path: Path) -> None:
    """標記檔案為已閱讀。"""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO files_read (file_path, read_at) VALUES (?, ?)",
        (str(path), now),
    )
    conn.commit()


def is_seen(source: str, item_id: str) -> bool:
    """檢查某 source 的 item（PMID、tweet id、url hash 等）是否已見過。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT 1 FROM scout_seen WHERE source = ? AND item_id = ?",
        (source, item_id),
    ).fetchone()
    return row is not None


def mark_seen(source: str, item_id: str, url: Optional[str] = None) -> None:
    """標記某 source 的 item 已見過（冪等：重複呼叫安全）。"""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT OR IGNORE INTO scout_seen (source, item_id, url, first_seen)
           VALUES (?, ?, ?, ?)""",
        (source, item_id, url, now),
    )
    conn.commit()


def start_run(agent: str) -> int:
    """記錄 agent 開始執行，回傳 run_id。"""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO agent_runs (agent, started_at, status) VALUES (?, ?, 'running')",
        (agent, now),
    )
    conn.commit()
    return cur.lastrowid


def finish_run(run_id: int, *, status: str = "done", summary: str = "") -> None:
    """記錄 agent 執行結束。"""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE agent_runs SET finished_at = ?, status = ?, summary = ? WHERE id = ?",
        (now, status, summary, run_id),
    )
    conn.commit()


def record_api_call(
    agent: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    run_id: Optional[int] = None,
    *,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> None:
    """記錄一次 Claude API 呼叫的 token 用量。

    ``input_tokens`` / ``output_tokens`` 對應 Anthropic response.usage 的
    input_tokens / output_tokens（thinking tokens 已含在 output）。
    Cache tokens 來自 ``cache_read_input_tokens`` / ``cache_creation_input_tokens``。
    """
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO api_calls
              (agent, run_id, model, input_tokens, output_tokens,
               cache_read_tokens, cache_write_tokens, called_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            agent,
            run_id,
            model,
            input_tokens,
            output_tokens,
            cache_read_tokens,
            cache_write_tokens,
            now,
        ),
    )
    # 同步累加到 agent_runs（只累計 input/output，cache 統計查 api_calls 即可）
    if run_id is not None:
        conn.execute(
            """UPDATE agent_runs
               SET input_tokens  = input_tokens  + ?,
                   output_tokens = output_tokens + ?
               WHERE id = ?""",
            (input_tokens, output_tokens, run_id),
        )
    conn.commit()


def get_cost_summary(agent: Optional[str] = None, days: int = 7) -> list[dict]:
    """回傳最近 N 天的 token 用量統計，依 agent 分組。

    Args:
        agent: 過濾特定 agent，None 表示全部
        days:  統計最近幾天

    Returns:
        列表，每筆含 agent, model, calls, input_tokens, output_tokens
    """
    conn = _get_conn()
    from datetime import timedelta

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    select = """SELECT agent, model,
                       COUNT(*) as calls,
                       SUM(input_tokens) as input_tokens,
                       SUM(output_tokens) as output_tokens,
                       SUM(cache_read_tokens) as cache_read_tokens,
                       SUM(cache_write_tokens) as cache_write_tokens
                FROM api_calls"""
    if agent:
        rows = conn.execute(
            f"""{select}
                WHERE agent = ? AND called_at >= ?
                GROUP BY agent, model
                ORDER BY calls DESC""",
            (agent, since),
        ).fetchall()
    else:
        rows = conn.execute(
            f"""{select}
                WHERE called_at >= ?
                GROUP BY agent, model
                ORDER BY calls DESC""",
            (since,),
        ).fetchall()

    return [dict(row) for row in rows]


def get_cost_timeseries(
    *,
    agent: Optional[str] = None,
    days: int = 7,
    bucket: str = "day",
) -> list[dict]:
    """回傳時間序列的 token 用量，按 agent × model × bucket 聚合。

    Args:
        agent:  過濾特定 agent，None 表示全部
        days:   統計最近幾天
        bucket: "day" 或 "hour"

    Returns:
        列表，每筆含 bucket (ISO str)、agent、model、tokens 欄位
    """
    if bucket not in ("day", "hour"):
        raise ValueError(f"bucket must be 'day' or 'hour', got {bucket!r}")

    conn = _get_conn()
    from datetime import timedelta

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    # SQLite substr 切 ISO8601：'2026-04-19T03:15:22...' → day 取前 10 碼、hour 取前 13 碼 + ':00'
    bucket_expr = (
        "substr(called_at, 1, 10)" if bucket == "day" else "substr(called_at, 1, 13) || ':00'"
    )

    params: list = [since]
    where = "called_at >= ?"
    if agent:
        where += " AND agent = ?"
        params.append(agent)

    rows = conn.execute(
        f"""SELECT {bucket_expr} as bucket,
                   agent, model,
                   COUNT(*) as calls,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens,
                   SUM(cache_read_tokens) as cache_read_tokens,
                   SUM(cache_write_tokens) as cache_write_tokens
            FROM api_calls
            WHERE {where}
            GROUP BY bucket, agent, model
            ORDER BY bucket ASC, agent ASC""",
        params,
    ).fetchall()

    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# ADR-002 Tier 3: 記憶搜尋層
# ---------------------------------------------------------------------------


def remember(
    agent: str,
    type: str,
    title: str,
    content: str,
    tags: Optional[list[str]] = None,
    confidence: str = "medium",
    source: Optional[str] = None,
    ttl_days: Optional[int] = None,
) -> int:
    """記錄一筆新記憶到 Tier 3（SQLite + FTS5）。

    Args:
        agent:      記錄者（robin, franky, claude, ...）
        type:       記憶類型（semantic, episodic, procedural, user）
        title:      簡短標題
        content:    記憶內容
        tags:       標籤列表
        confidence: 信心度（high, medium, low）
        source:     來源（session_id, run_id, file_path, ...）
        ttl_days:   幾天後過期（None 表示永久）

    Returns:
        新記憶的 id
    """
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    expires_at = None
    if ttl_days is not None:
        from datetime import timedelta

        expires_at = (datetime.now(timezone.utc) + timedelta(days=ttl_days)).isoformat()

    cur = conn.execute(
        """INSERT INTO memories
           (agent, type, title, content, tags, confidence, source,
            created_at, updated_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            agent,
            type,
            title,
            content,
            json.dumps(tags or [], ensure_ascii=False),
            confidence,
            source,
            now,
            now,
            expires_at,
        ),
    )
    conn.commit()
    return cur.lastrowid


def search_memory(
    query: str,
    agent: Optional[str] = None,
    type: Optional[str] = None,
    limit: int = 5,
) -> list[dict]:
    """FTS5 全文搜尋記憶。

    Args:
        query:  搜尋關鍵字（支援 FTS5 語法：AND, OR, NOT, "phrase"）
        agent:  過濾特定 agent（None 表示全部）
        type:   過濾特定類型（None 表示全部）
        limit:  最多回傳幾筆

    Returns:
        列表，每筆含 id, agent, type, title, content, tags, confidence, created_at
        依相關度排序。
    """
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()

    # 建構 WHERE 子句
    conditions = ["m.id IN (SELECT rowid FROM memories_fts WHERE memories_fts MATCH ?)"]
    params: list = [query]

    # 排除過期記憶
    conditions.append("(m.expires_at IS NULL OR m.expires_at > ?)")
    params.append(now)

    if agent:
        conditions.append("m.agent = ?")
        params.append(agent)
    if type:
        conditions.append("m.type = ?")
        params.append(type)

    where = " AND ".join(conditions)
    params.append(limit)

    rows = conn.execute(
        f"""SELECT m.id, m.agent, m.type, m.title, m.content,
                   m.tags, m.confidence, m.source, m.created_at
            FROM memories m
            WHERE {where}
            ORDER BY m.created_at DESC
            LIMIT ?""",
        params,
    ).fetchall()

    results = []
    for row in rows:
        d = dict(row)
        d["tags"] = json.loads(d["tags"]) if d["tags"] else []
        results.append(d)
    return results


def list_memories(
    agent: Optional[str] = None,
    type: Optional[str] = None,
    limit: int = 20,
) -> list[dict]:
    """列出記憶（不需搜尋關鍵字）。

    Args:
        agent:  過濾特定 agent（None 表示全部）
        type:   過濾特定類型（None 表示全部）
        limit:  最多回傳幾筆

    Returns:
        列表，依建立時間倒序。
    """
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()

    conditions = ["(expires_at IS NULL OR expires_at > ?)"]
    params: list = [now]

    if agent:
        conditions.append("agent = ?")
        params.append(agent)
    if type:
        conditions.append("type = ?")
        params.append(type)

    where = " AND ".join(conditions)
    params.append(limit)

    rows = conn.execute(
        f"""SELECT id, agent, type, title, content, tags, confidence, source, created_at
            FROM memories
            WHERE {where}
            ORDER BY created_at DESC
            LIMIT ?""",
        params,
    ).fetchall()

    results = []
    for row in rows:
        d = dict(row)
        d["tags"] = json.loads(d["tags"]) if d["tags"] else []
        results.append(d)
    return results
