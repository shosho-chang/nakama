"""統一 logging：Python logger + KB/log.md 寫入。"""

import logging
import sys
from datetime import datetime, timezone

from shared.obsidian_writer import append_to_file

# ── Python logger ──

_initialized = False


def get_logger(name: str = "nakama") -> logging.Logger:
    global _initialized
    if not _initialized:
        # 設定 root nakama logger 的 handler（只做一次）
        root = logging.getLogger("nakama")
        root.setLevel(logging.INFO)
        handler = logging.StreamHandler(sys.stdout)
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
