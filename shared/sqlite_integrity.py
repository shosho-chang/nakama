"""SQLite snapshot integrity check — used by both the restore tool and the
weekly backup-integrity cron.

Single-purpose: open a `.db` file, run `PRAGMA integrity_check`, count tables
+ rows, return a verdict. Designed to never raise on a corrupt file — operators
get a clean (False, 0, 0) and an error log line, not a stack trace.

`scripts/restore_from_r2.py` (PR #146) duplicates this logic inline; once that
PR merges, a small follow-up will swap the inline copy for an import. Keeping
this module the canonical version here so Phase 2B's
`scripts/verify_backup_integrity.py` doesn't have to wait for the merge.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from shared.log import get_logger

logger = get_logger("nakama.sqlite_integrity")


def verify_db(db_path: Path) -> tuple[bool, int, int]:
    """Run `PRAGMA integrity_check` + count tables + sum rows.

    Returns (integrity_ok, table_count, row_count_total). A 0-byte sentinel
    file (legitimate for `nakama.db` today) returns (True, 0, 0). Any sqlite
    error (header garbage, page corruption) returns (False, 0, 0) so operators
    see a clean "integrity FAILED" report rather than a stack trace.
    """
    if db_path.stat().st_size == 0:
        return True, 0, 0

    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.DatabaseError as exc:
        logger.error("sqlite open failed: %s", exc)
        return False, 0, 0

    try:
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()
        except sqlite3.DatabaseError as exc:
            logger.error("integrity_check raised: %s", exc)
            return False, 0, 0
        integrity_ok = bool(integrity) and integrity[0] == "ok"

        try:
            tables = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            logger.error("table listing raised: %s", exc)
            return False, 0, 0
        table_count = len(tables)

        # Sum row counts across every user table — proves the file is readable
        # end-to-end, not just header-valid. Any error here means we can't
        # trust the snapshot.
        total_rows = 0
        for (tname,) in tables:
            try:
                (n,) = conn.execute(f"SELECT COUNT(*) FROM {tname}").fetchone()
            except sqlite3.DatabaseError as exc:
                logger.error("row count failed table=%s: %s", tname, exc)
                return False, table_count, total_rows
            total_rows += n
    finally:
        conn.close()

    return integrity_ok, table_count, total_rows
