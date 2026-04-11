"""記憶維護工具（ADR-002 Phase 3）。

功能：
  1. expire  — 清理已過期的記憶（TTL 到期）
  2. archive — 將舊的低信心度記憶移至 archive
  3. stats   — 顯示記憶統計

用法：
    python -m shared.memory_maintenance expire
    python -m shared.memory_maintenance archive --days 90
    python -m shared.memory_maintenance stats
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

from shared.state import _get_conn


def expire_memories() -> int:
    """刪除已過期的記憶（expires_at < now）。回傳刪除筆數。"""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()

    # 先取得要刪除的 id（FTS5 觸發器會自動同步）
    rows = conn.execute(
        "SELECT id, agent, title FROM memories WHERE expires_at IS NOT NULL AND expires_at < ?",
        (now,),
    ).fetchall()

    if not rows:
        return 0

    ids = [r["id"] for r in rows]
    conn.execute(
        f"DELETE FROM memories WHERE id IN ({','.join('?' * len(ids))})",
        ids,
    )
    conn.commit()

    for r in rows:
        print(f"  expired: [{r['agent']}] {r['title']}")

    return len(rows)


def archive_old_memories(days: int = 90, confidence: str = "low") -> int:
    """將超過 N 天且信心度低的記憶標記為過期（設 expires_at 為現在）。

    不直接刪除，下次 expire 時才清理。回傳歸檔筆數。
    """
    conn = _get_conn()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    now = datetime.now(timezone.utc).isoformat()

    rows = conn.execute(
        """SELECT id, agent, title FROM memories
           WHERE created_at < ? AND confidence = ?
           AND (expires_at IS NULL OR expires_at > ?)""",
        (cutoff, confidence, now),
    ).fetchall()

    if not rows:
        return 0

    ids = [r["id"] for r in rows]
    conn.execute(
        f"UPDATE memories SET expires_at = ? WHERE id IN ({','.join('?' * len(ids))})",
        [now] + ids,
    )
    conn.commit()

    for r in rows:
        print(f"  archived: [{r['agent']}] {r['title']}")

    return len(rows)


def memory_stats() -> dict:
    """回傳記憶統計資訊。"""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()

    total = conn.execute("SELECT COUNT(*) as c FROM memories").fetchone()["c"]

    active = conn.execute(
        "SELECT COUNT(*) as c FROM memories WHERE expires_at IS NULL OR expires_at > ?",
        (now,),
    ).fetchone()["c"]

    expired = total - active

    # 各 agent 統計
    by_agent = conn.execute(
        """SELECT agent, COUNT(*) as c FROM memories
           WHERE expires_at IS NULL OR expires_at > ?
           GROUP BY agent ORDER BY c DESC""",
        (now,),
    ).fetchall()

    # 各 type 統計
    by_type = conn.execute(
        """SELECT type, COUNT(*) as c FROM memories
           WHERE expires_at IS NULL OR expires_at > ?
           GROUP BY type ORDER BY c DESC""",
        (now,),
    ).fetchall()

    # 各 confidence 統計
    by_conf = conn.execute(
        """SELECT confidence, COUNT(*) as c FROM memories
           WHERE expires_at IS NULL OR expires_at > ?
           GROUP BY confidence ORDER BY c DESC""",
        (now,),
    ).fetchall()

    return {
        "total": total,
        "active": active,
        "expired": expired,
        "by_agent": {r["agent"]: r["c"] for r in by_agent},
        "by_type": {r["type"]: r["c"] for r in by_type},
        "by_confidence": {r["confidence"]: r["c"] for r in by_conf},
    }


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]

    if cmd == "expire":
        n = expire_memories()
        print(f"已清理 {n} 筆過期記憶")

    elif cmd == "archive":
        days = 90
        if "--days" in sys.argv:
            idx = sys.argv.index("--days")
            days = int(sys.argv[idx + 1])
        n = archive_old_memories(days=days)
        print(f"已歸檔 {n} 筆舊記憶（>{days} 天, confidence=low）")

    elif cmd == "stats":
        s = memory_stats()
        print(f"記憶統計：")
        print(f"  總數: {s['total']}（活躍: {s['active']}, 過期: {s['expired']}）")
        if s["by_agent"]:
            print(f"  依 agent: {s['by_agent']}")
        if s["by_type"]:
            print(f"  依 type:  {s['by_type']}")
        if s["by_confidence"]:
            print(f"  依信心度: {s['by_confidence']}")

    else:
        print(f"未知指令: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
