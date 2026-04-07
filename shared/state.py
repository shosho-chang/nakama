"""SQLite 狀態管理：追蹤已處理檔案、agent 執行紀錄。"""

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from shared.config import get_db_path

_conn: sqlite3.Connection | None = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        db_path = get_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(db_path))
        _conn.row_factory = sqlite3.Row
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
            summary     TEXT
        );

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
    """)
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
