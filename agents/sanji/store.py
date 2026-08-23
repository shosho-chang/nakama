"""Sanji 的本地狀態（cursor、判定佇列、打卡日投影）——住 nakama state.db。

⚠️ 這裡只放**可重建的衍生狀態**：
- cursor 掉了 → 從 0 重拉，plugin 端 idempotency 保證不重複入帳（只是多花時間）
- 打卡日投影掉了 → 由 WP 端 grants 表（source=checkin_day）重建
- 判定佇列掉了 → 未回覆的打卡由每日對帳掃出重排
帳本唯一真相源永遠在 WP MySQL 的 grants 表（agents/sanji/CONTEXT.md 裁決）。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator

from shared.config import get_db_path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sanji_cursors (
    name  TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sanji_checkin_days (
    user_id INTEGER NOT NULL,
    day     TEXT    NOT NULL,          -- YYYY-MM-DD（站台時區）
    season  TEXT    NOT NULL,
    feed_id INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);
CREATE TABLE IF NOT EXISTS sanji_judgments (
    event_id   INTEGER PRIMARY KEY,    -- 對應 plugin events.id
    feed_id    INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    day        TEXT    NOT NULL,
    season     TEXT    NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'pending',
        -- pending / approved / rejected / auto_approved_by_timeout
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    decided_at TEXT,
    note       TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sanji_judgments_status ON sanji_judgments (status);
"""


class Store:
    def __init__(self, db_path: Path | None = None):
        self._path = db_path or get_db_path()
        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ── cursors ──────────────────────────────────────────────────
    def get_cursor(self, name: str) -> int:
        row = self._conn.execute(
            "SELECT value FROM sanji_cursors WHERE name = ?", (name,)
        ).fetchone()
        return int(row["value"]) if row else 0

    def set_cursor(self, name: str, value: int) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO sanji_cursors (name, value) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET value = excluded.value",
                (name, int(value)),
            )

    # ── 打卡日投影（streak 計算用） ───────────────────────────────
    def record_checkin_day(self, user_id: int, day: str, season: str, feed_id: int) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT OR IGNORE INTO sanji_checkin_days (user_id, day, season, feed_id) "
                "VALUES (?, ?, ?, ?)",
                (user_id, day, season, feed_id),
            )

    def has_checkin(self, user_id: int, day: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sanji_checkin_days WHERE user_id = ? AND day = ?", (user_id, day)
        ).fetchone()
        return row is not None

    def current_streak(self, user_id: int, day: str) -> int:
        """以 ``day``（含）為終點往回數的連續打卡天數。斷一天即停。"""
        d = date.fromisoformat(day)
        streak = 0
        while self.has_checkin(user_id, d.isoformat()):
            streak += 1
            d -= timedelta(days=1)
        return streak

    def has_any_checkin_before(self, user_id: int, day: str) -> bool:
        """``day`` 之前有沒有任何打卡史（回歸語氣 vs 新人語氣的分界）。"""
        row = self._conn.execute(
            "SELECT 1 FROM sanji_checkin_days WHERE user_id = ? AND day < ? LIMIT 1",
            (user_id, day),
        ).fetchone()
        return row is not None

    def users_checked_in_on(self, day: str) -> list[int]:
        """某日有打卡紀錄的使用者（每日對帳的抽核對象）。"""
        rows = self._conn.execute(
            "SELECT DISTINCT user_id FROM sanji_checkin_days WHERE day = ?", (day,)
        ).fetchall()
        return [int(r["user_id"]) for r in rows]

    # ── 判定佇列（漏斗 ⑥⑦：provisional / 48h fail-open） ─────────
    def enqueue_judgment(
        self, event_id: int, feed_id: int, user_id: int, day: str, season: str
    ) -> bool:
        """排入待判定。回傳 False = 已存在（重放）。"""
        with self._tx() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO sanji_judgments (event_id, feed_id, user_id, day, season) "
                "VALUES (?, ?, ?, ?, ?)",
                (event_id, feed_id, user_id, day, season),
            )
            return cur.rowcount > 0

    def decide(self, event_id: int, status: str, note: str = "") -> None:
        assert status in {"approved", "rejected", "auto_approved_by_timeout"}
        with self._tx() as c:
            c.execute(
                "UPDATE sanji_judgments SET status = ?, decided_at = datetime('now'), note = ? "
                "WHERE event_id = ?",
                (status, note, event_id),
            )

    def pending(self, *, limit: int = 50) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT * FROM sanji_judgments WHERE status = 'pending' "
                "ORDER BY event_id ASC LIMIT ?",
                (limit,),
            )
        )

    def pending_older_than_hours(self, hours: int) -> list[sqlite3.Row]:
        """fail-open 掃描：滯留超過 N 小時的待判定案。"""
        return list(
            self._conn.execute(
                "SELECT * FROM sanji_judgments WHERE status = 'pending' "
                "AND created_at <= datetime('now', ?) ORDER BY event_id ASC",
                (f"-{int(hours)} hours",),
            )
        )
