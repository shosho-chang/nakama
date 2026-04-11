"""統一 logging：Python logger + KB/log.md 寫入。"""

import logging
import sys
from datetime import datetime, timezone

from shared.obsidian_writer import append_to_file

# ── Python logger ──

_logger: logging.Logger | None = None


def get_logger(name: str = "nakama") -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger

    _logger = logging.getLogger(name)
    _logger.setLevel(logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(name)s %(levelname)s — %(message)s"))
    _logger.addHandler(handler)
    return _logger


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
