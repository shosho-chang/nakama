"""SQLite FTS5 index over structured nakama logs.

Powers `/bridge/logs` search (Phase 5C). Lives in its own SQLite file
(`data/logs.db`) so it doesn't pollute `state.db` and can be wiped/rebuilt
without touching agent state. Backup strategy differs (logs are 30d retention,
state.db is permanent), and VACUUM on hot log table doesn't stall agents.

Schema:

    CREATE TABLE logs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,             -- ISO8601 UTC, second precision
        ts_unix REAL NOT NULL,        -- range query / ORDER BY index
        level TEXT NOT NULL,          -- INFO / WARNING / ERROR / CRITICAL / DEBUG
        logger TEXT NOT NULL,         -- 'nakama.X.Y' dotted name
        msg TEXT NOT NULL,
        extra_json TEXT NOT NULL DEFAULT '{}'
    );
    CREATE VIRTUAL TABLE logs_fts USING fts5(
        msg, extra_json,
        content='logs', content_rowid='id',
        tokenize='porter unicode61'
    );

Tokenizer notes mirror `shared.doc_index`: unicode61 handles CJK as 1-char
tokens (built-in, zero-dep), porter stems EN.

Usage (test / cron / cleanup):
    from shared.log_index import LogIndex
    idx = LogIndex.from_default_path()
    idx.insert(ts=now_utc(), level="INFO", logger="nakama.test", msg="hi", extra={})
    hits = idx.search("hi", limit=10)

The Python `logging.Handler` integration that writes here lives in
`shared.log:SQLiteLogHandler`.
"""

from __future__ import annotations

import html
import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Sentinel tokens — same scheme as `shared.doc_index`. We html.escape() the
# whole snippet, then swap these back to `<mark>` so log msg literals like
# `<script>` can't escape into the template via `| safe`.
_MARK_OPEN = "\x01"
_MARK_CLOSE = "\x02"


@dataclass(frozen=True)
class LogHit:
    """One ranked / time-ordered search result.

    `snippet` is safe HTML — already html.escape()'d, with only `<mark>` /
    `</mark>` tags re-introduced. Templates render with `| safe`.
    """

    id: int
    ts: str
    level: str
    logger: str
    msg: str
    snippet: str
    extra: dict


@dataclass(frozen=True)
class LogStats:
    total: int
    by_level: dict[str, int] = field(default_factory=dict)
    oldest_ts: str | None = None
    newest_ts: str | None = None


def _safe_snippet(raw: str) -> str:
    return html.escape(raw).replace(_MARK_OPEN, "<mark>").replace(_MARK_CLOSE, "</mark>")


class LogIndex:
    """SQLite FTS5 + metadata index for structured logs."""

    def __init__(self, *, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    @classmethod
    def from_default_path(cls) -> LogIndex:
        """Resolve db path; mirror `shared.doc_index.from_repo_root` precedence.

        Path resolution (first match wins):
          1. `NAKAMA_LOG_DB_PATH` env override (full path) — for tests
          2. `NAKAMA_DATA_DIR` env (data dir, file appended) — VPS sets this
          3. `<repo_root>/data/logs.db` — local dev fallback
        """
        override = os.environ.get("NAKAMA_LOG_DB_PATH")
        if override:
            return cls(db_path=Path(override))
        data_dir_env = os.environ.get("NAKAMA_DATA_DIR")
        if data_dir_env:
            return cls(db_path=Path(data_dir_env) / "logs.db")
        repo_root = Path(__file__).resolve().parent.parent
        return cls(db_path=repo_root / "data" / "logs.db")

    @classmethod
    def from_path(cls, db_path: Path) -> LogIndex:
        return cls(db_path=db_path)

    # ---- connection / schema -------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            # WAL mode lets the log handler write while /bridge/logs reads
            # without lock contention. check_same_thread=False because the
            # handler emits from the logger thread, the bridge route reads
            # from the request thread.
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")  # WAL + NORMAL is safe + fast
            self._init_schema(conn)
            self._conn = conn
        return self._conn

    @staticmethod
    def _init_schema(conn: sqlite3.Connection) -> None:
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS logs(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    ts_unix REAL NOT NULL,
                    level TEXT NOT NULL,
                    logger TEXT NOT NULL,
                    msg TEXT NOT NULL,
                    extra_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_logs_ts_unix ON logs(ts_unix DESC);
                CREATE INDEX IF NOT EXISTS idx_logs_level_ts ON logs(level, ts_unix DESC);
                CREATE INDEX IF NOT EXISTS idx_logs_logger_ts ON logs(logger, ts_unix DESC);
                CREATE VIRTUAL TABLE IF NOT EXISTS logs_fts USING fts5(
                    msg, extra_json,
                    content='logs', content_rowid='id',
                    tokenize='porter unicode61'
                );
                CREATE TRIGGER IF NOT EXISTS logs_ai AFTER INSERT ON logs BEGIN
                    INSERT INTO logs_fts(rowid, msg, extra_json)
                    VALUES (new.id, new.msg, new.extra_json);
                END;
                CREATE TRIGGER IF NOT EXISTS logs_ad AFTER DELETE ON logs BEGIN
                    INSERT INTO logs_fts(logs_fts, rowid, msg, extra_json)
                    VALUES ('delete', old.id, old.msg, old.extra_json);
                END;
                """
            )
            conn.commit()
        except sqlite3.OperationalError as exc:
            raise RuntimeError(f"FTS5 not available in this sqlite build: {exc}") from exc

    # ---- public API ----------------------------------------------------------

    def insert(
        self,
        *,
        ts: datetime,
        level: str,
        logger: str,
        msg: str,
        extra: dict,
    ) -> int:
        """Insert one log row. Returns the new row id.

        `ts` must be timezone-aware UTC; assertion enforces this so naïve
        datetimes that happen to be local time don't silently shift on insert.
        """
        if ts.tzinfo is None:
            raise ValueError("ts must be timezone-aware (use datetime.now(timezone.utc))")
        ts_utc = ts.astimezone(timezone.utc)
        conn = self._get_conn()
        cur = conn.execute(
            "INSERT INTO logs (ts, ts_unix, level, logger, msg, extra_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                ts_utc.isoformat(timespec="seconds"),
                ts_utc.timestamp(),
                level,
                logger,
                msg,
                json.dumps(extra, default=str, ensure_ascii=False) if extra else "{}",
            ),
        )
        conn.commit()
        return int(cur.lastrowid)

    def search(
        self,
        query: str,
        *,
        level: str | None = None,
        logger_prefix: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[LogHit]:
        """Search by FTS5 + structured filters.

        - `query` empty string → falls back to time-ordered list (no FTS).
        - Bad FTS5 syntax → returns [] with WARNING log; mirrors doc_index.
        - `logger_prefix='nakama.franky'` matches `nakama.franky.*` (LIKE prefix).
        """
        conn = self._get_conn()
        params: list = []
        where_clauses: list[str] = []

        if query.strip():
            # FTS path: join against logs_fts, snippet from msg column (idx 0).
            snippet_expr = f"snippet(logs_fts, 0, '{_MARK_OPEN}', '{_MARK_CLOSE}', ' … ', 15)"
            sql = (
                "SELECT logs.id, logs.ts, logs.level, logs.logger, logs.msg, logs.extra_json, "
                f"       {snippet_expr} AS snippet "
                "FROM logs JOIN logs_fts ON logs_fts.rowid = logs.id "
                "WHERE logs_fts MATCH ?"
            )
            params.append(query)
        else:
            # No FTS — filter-only timeline browse.
            sql = (
                "SELECT id, ts, level, logger, msg, extra_json, "
                "       msg AS snippet "
                "FROM logs WHERE 1=1"
            )

        if level:
            where_clauses.append("logs.level = ?" if query.strip() else "level = ?")
            params.append(level.upper())
        if logger_prefix:
            col = "logs.logger" if query.strip() else "logger"
            where_clauses.append(f"{col} LIKE ?")
            params.append(f"{logger_prefix}%")
        if since:
            col = "logs.ts_unix" if query.strip() else "ts_unix"
            where_clauses.append(f"{col} >= ?")
            params.append(since.astimezone(timezone.utc).timestamp())
        if until:
            col = "logs.ts_unix" if query.strip() else "ts_unix"
            where_clauses.append(f"{col} <= ?")
            params.append(until.astimezone(timezone.utc).timestamp())

        if where_clauses:
            sql += " AND " + " AND ".join(where_clauses)

        if query.strip():
            # FTS rank + tie-break by recency.
            sql += " ORDER BY bm25(logs_fts), logs.ts_unix DESC"
        else:
            sql += " ORDER BY ts_unix DESC"
        sql += " LIMIT ? OFFSET ?"
        params.extend([min(limit, 500), max(offset, 0)])

        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            # Bad FTS5 query syntax — soft-fail, matches doc_index behavior.
            from shared.log import get_logger

            get_logger("nakama.log_index").warning("fts5 query syntax err query=%r: %s", query, exc)
            return []
        return [
            LogHit(
                id=int(row["id"]),
                ts=row["ts"],
                level=row["level"],
                logger=row["logger"],
                msg=row["msg"],
                snippet=_safe_snippet(row["snippet"]),
                extra=json.loads(row["extra_json"]) if row["extra_json"] else {},
            )
            for row in rows
        ]

    def count_by_hour(
        self,
        *,
        since: datetime,
        until: datetime,
        levels: tuple[str, ...] | None = None,
    ) -> dict[str, int]:
        """Return {hour_bucket_iso: count} over `[since, until)` (UTC),
        bucketed by ``strftime('%Y-%m-%dT%H', ts)``.

        Phase 5B-3 anomaly daemon uses this for baseline error-rate
        aggregation (with `levels=('ERROR', 'CRITICAL')`) and for the
        "active baseline hours" set (without levels filter, so silent
        hours don't poison the baseline).

        Half-open range: rows with ts == until are excluded. Hour bucket
        keys look like ``"2026-04-26T14"`` — UTC, no timezone suffix
        (sqlite's strftime drops it).
        """
        conn = self._get_conn()
        sql = (
            "SELECT strftime('%Y-%m-%dT%H', ts) AS hour_bucket, COUNT(*) AS n "
            "FROM logs WHERE ts >= ? AND ts < ?"
        )
        params: list = [
            since.astimezone(timezone.utc).isoformat(timespec="seconds"),
            until.astimezone(timezone.utc).isoformat(timespec="seconds"),
        ]
        if levels:
            placeholders = ",".join("?" * len(levels))
            sql += f" AND level IN ({placeholders})"
            params.extend(levels)
        sql += " GROUP BY hour_bucket"
        rows = conn.execute(sql, params).fetchall()
        return {row["hour_bucket"]: int(row["n"]) for row in rows}

    def stats(self) -> LogStats:
        conn = self._get_conn()
        total_row = conn.execute("SELECT COUNT(*) AS n FROM logs").fetchone()
        total = int(total_row["n"]) if total_row else 0
        if total == 0:
            return LogStats(total=0)

        by_level_rows = conn.execute(
            "SELECT level, COUNT(*) AS n FROM logs GROUP BY level"
        ).fetchall()
        by_level = {row["level"]: int(row["n"]) for row in by_level_rows}

        bounds = conn.execute("SELECT MIN(ts) AS oldest, MAX(ts) AS newest FROM logs").fetchone()
        return LogStats(
            total=total,
            by_level=by_level,
            oldest_ts=bounds["oldest"] if bounds else None,
            newest_ts=bounds["newest"] if bounds else None,
        )

    def cleanup(self, *, older_than: timedelta) -> int:
        """Delete rows older than now - older_than (UTC). Returns deleted row count.

        Trigger `logs_ad` keeps logs_fts in sync. Caller should run VACUUM after
        large deletes to reclaim disk; we don't VACUUM here because it locks the
        whole DB and the cleanup cron has its own VACUUM step.
        """
        cutoff = (datetime.now(timezone.utc) - older_than).timestamp()
        conn = self._get_conn()
        cur = conn.execute("DELETE FROM logs WHERE ts_unix < ?", (cutoff,))
        conn.commit()
        return int(cur.rowcount)

    def vacuum(self) -> None:
        conn = self._get_conn()
        conn.execute("VACUUM")

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
