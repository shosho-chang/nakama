"""統一 logging：Python logger + KB/log.md 寫入。

`NAKAMA_LOG_FORMAT=json` 切到 JSON-line output（VPS observability 用）。
未設或 `text` 維持人類可讀格式（dev / CI 預設）。
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone

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
        for key, value in record.__dict__.items():
            if key in _STANDARD_LOG_FIELDS or key.startswith("_"):
                continue
            payload[key] = value
        return json.dumps(payload, ensure_ascii=False, default=str)


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
