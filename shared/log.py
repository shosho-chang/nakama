"""統一 logging：Python logger + KB/log.md 寫入 + SQLite FTS5 search index.

`NAKAMA_LOG_FORMAT=json` 切到 JSON-line output（VPS observability 用）。
未設或 `text` 維持人類可讀格式（dev / CI 預設）。

`NAKAMA_LOG_DB_DISABLE=1` 跳過 SQLite log index handler（CI / unit tests
should set this in conftest to avoid polluting `data/logs.db`).
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from shared.obsidian_writer import append_to_file

# ── Python logger ──

_initialized = False

# Standard `LogRecord` attribute set — anything OUTSIDE this is treated as
# user-supplied `extra={...}` fields and emitted into the JSON payload as
# top-level keys. Lets callers attach structured context like
#   logger.info("backup ok", extra={"job": "nakama-backup", "tier": "daily"})
# without colliding with logger internals.
_STANDARD_LOG_FIELDS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "getMessage",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",  # Python 3.12+ asyncio
    }
)


def _extract_extra(record: logging.LogRecord) -> dict:
    """Pull caller-supplied `extra={...}` fields off a LogRecord.

    Anything OUTSIDE _STANDARD_LOG_FIELDS that doesn't start with `_` is
    treated as user-supplied structured context. Used by both JSONFormatter
    and SQLiteLogHandler so the same fields reach stdout JSON and the
    `/bridge/logs` extra column.
    """
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in _STANDARD_LOG_FIELDS and not key.startswith("_")
    }


class JSONFormatter(logging.Formatter):
    """Emit a single JSON object per log record (newline-delimited).

    Output schema (stable across releases — observability tools depend on it):
    - ts:     ISO8601 UTC ("2026-04-25T19:38:15.285Z")
    - level:  string ("INFO", "WARNING", ...)
    - logger: dotted logger name ("nakama.backup")
    - msg:    rendered message string
    - exc:    formatted traceback if record had exc_info (optional)
    - <key>:  any extra={...} fields supplied by caller (string-coerced)
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        payload.update(_extract_extra(record))
        return json.dumps(payload, ensure_ascii=False, default=str)


class SQLiteLogHandler(logging.Handler):
    """Persist log records to `data/logs.db` for `/bridge/logs` FTS search.

    Synchronous insert per record (WAL mode keeps p99 < a few ms). DEBUG is
    filtered via the handler's level threshold (default INFO) — debug volume
    has low signal-to-noise for postmortem search.

    On any insert error, falls back to `self.handleError(record)` which by
    default prints to stderr and continues. This guarantees a broken log DB
    can never crash the calling code path.
    """

    def __init__(
        self,
        *,
        level: int = logging.INFO,
        db_path: Path | None = None,
    ) -> None:
        super().__init__(level=level)
        self._db_path = db_path
        self._index = None

    def _get_index(self):
        # Lazy init so handler can be constructed in CI / tests without
        # touching the filesystem until something actually emits.
        if self._index is None:
            from shared.log_index import LogIndex

            self._index = (
                LogIndex.from_path(self._db_path)
                if self._db_path is not None
                else LogIndex.from_default_path()
            )
        return self._index

    def emit(self, record: logging.LogRecord) -> None:
        try:
            extra = _extract_extra(record)
            # `logger.exception()` sets exc_info; capture the traceback into
            # extra so /bridge/logs can search inside it (FTS5 indexes
            # extra_json). Without this, the most useful postmortem log
            # type loses its stack trace at insert time.
            if record.exc_info:
                extra["exc"] = self.format_exc(record.exc_info)
            self._get_index().insert(
                ts=datetime.fromtimestamp(record.created, tz=timezone.utc),
                level=record.levelname,
                logger=record.name,
                msg=record.getMessage(),
                extra=extra,
            )
        except Exception:
            # Logging itself must NEVER raise — fall back to stderr via
            # logging.Handler default behavior.
            self.handleError(record)

    @staticmethod
    def format_exc(exc_info) -> str:
        """Format exc_info via the standard `Formatter.formatException` route.
        Wrapped as a method so tests can monkeypatch if needed."""
        return logging.Formatter().formatException(exc_info)


def get_logger(name: str = "nakama") -> logging.Logger:
    global _initialized
    if not _initialized:
        # Lazy-load .env so module-level `logger = get_logger(...)` (e.g. in
        # `scripts/backup_nakama_state.py`) sees `NAKAMA_LOG_FORMAT=json` even
        # when the caller's `main()` hasn't run `load_config()` yet. Without
        # this, the first call locks `_initialized=True` with the default text
        # formatter and any later .env load is silently ignored.
        from shared.config import load_config

        load_config()

        # 設定 root nakama logger 的 handler（只做一次）
        root = logging.getLogger("nakama")
        root.setLevel(logging.INFO)
        handler = logging.StreamHandler(sys.stdout)
        if os.environ.get("NAKAMA_LOG_FORMAT", "text").lower() == "json":
            handler.setFormatter(JSONFormatter())
        else:
            handler.setFormatter(
                logging.Formatter("[%(asctime)s] %(name)s %(levelname)s — %(message)s")
            )
        root.addHandler(handler)

        # SQLite FTS5 sink for /bridge/logs search (Phase 5C). Disable in CI /
        # tests via NAKAMA_LOG_DB_DISABLE=1 to avoid polluting data/logs.db.
        if os.environ.get("NAKAMA_LOG_DB_DISABLE", "").lower() not in ("1", "true", "yes"):
            root.addHandler(SQLiteLogHandler(level=logging.INFO))

        _initialized = True

    return logging.getLogger(name)


# ── KB/log.md append ──


def kb_log(agent: str, action: str, details: str = "") -> None:
    """在 KB/log.md 追加一筆操作紀錄。

    格式：
    - [2026-04-07 02:15] **robin** — ingest: 處理了 xxx.pdf，建立 Source Summary
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    line = f"- [{now}] **{agent}** — {action}"
    if details:
        line += f": {details}"
    line += "\n"
    append_to_file("KB/log.md", line)
