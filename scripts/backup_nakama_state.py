"""Daily snapshot of Nakama state DBs into the R2 nakama-backup bucket.

Uses SQLite's online `.backup` API to get an atomic, non-blocking copy of
`state.db`, gzip-compresses it, and uploads to
`nakama-backup/state/YYYY/MM/DD/state.db.gz` (same layout for `nakama.db`).

Retention: objects older than `NAKAMA_BACKUP_RETENTION_DAYS` (default 30)
under the backed-up prefix are deleted after a successful upload. Nothing
deletes on failure — the latest good snapshot is always preserved.

Cron:
    0 4 * * *  cd /home/nakama && python3 scripts/backup_nakama_state.py \\
        >> /var/log/nakama/nakama-backup.log 2>&1

Exit codes:
    0 — all configured DBs uploaded (or gracefully skipped if missing/empty)
    1 — at least one DB failed; check log, preserve service, try again next tick
"""

from __future__ import annotations

import gzip
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from shared.alerts import alert
from shared.config import load_config
from shared.heartbeat import record_failure, record_success
from shared.log import get_logger
from shared.r2_client import R2Client, R2Unavailable

logger = get_logger("nakama.backup")

_JOB_NAME = "nakama-backup"  # heartbeat key — keep stable across releases

# `nakama.db` is currently unused (0 bytes) but lives alongside state.db and was in
# the original Phase 1 layout; back it up too so future use doesn't silently bypass.
_DBS = ("state.db", "nakama.db")


def _backup_sqlite(src: Path, dst: Path) -> None:
    """Run SQLite's online `.backup` API — atomic snapshot even while writers hold the DB."""
    src_conn = sqlite3.connect(str(src))
    try:
        dst_conn = sqlite3.connect(str(dst))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def _gzip_file(src: Path, dst: Path) -> None:
    with open(src, "rb") as f_in, gzip.open(dst, "wb", compresslevel=6) as f_out:
        shutil.copyfileobj(f_in, f_out)


def _backup_one(data_dir: Path, db_name: str, client: R2Client, now: datetime) -> bool:
    """Returns True on success (uploaded or gracefully skipped); False on real failure."""
    src = data_dir / db_name
    if not src.exists():
        logger.info("skip db=%s reason=not_found", db_name)
        return True
    if src.stat().st_size == 0:
        logger.info("skip db=%s reason=empty", db_name)
        return True

    stem = db_name.removesuffix(".db")  # "state" / "nakama"
    key = f"{stem}/{now:%Y/%m/%d}/{db_name}.gz"

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        snapshot = tmp / db_name
        try:
            _backup_sqlite(src, snapshot)
        except sqlite3.Error as exc:
            logger.error("sqlite .backup failed db=%s err=%s", db_name, exc)
            return False

        gz = tmp / f"{db_name}.gz"
        _gzip_file(snapshot, gz)
        size_bytes = gz.stat().st_size
        logger.info(
            "db=%s snapshot=%d gz=%d ratio=%.2fx",
            db_name,
            snapshot.stat().st_size,
            size_bytes,
            snapshot.stat().st_size / max(size_bytes, 1),
        )

        try:
            client.upload_file(gz, key, content_type="application/gzip")
        except R2Unavailable as exc:
            logger.error("upload failed db=%s key=%s err=%s", db_name, key, exc)
            return False

    return True


def main() -> int:
    load_config()

    try:
        client = R2Client.from_nakama_backup_env()
    except R2Unavailable as exc:
        logger.error("r2 client unavailable: %s", exc)
        record_failure(_JOB_NAME, f"r2 client unavailable: {exc}")
        alert("error", "backup", f"R2 unavailable: {exc}", dedupe_key="backup-r2-unavailable")
        return 1

    data_dir = Path(os.environ.get("NAKAMA_DATA_DIR", "/home/nakama/data"))
    if not data_dir.is_dir():
        logger.error("data dir missing: %s", data_dir)
        record_failure(_JOB_NAME, f"data dir missing: {data_dir}")
        alert("error", "backup", f"data dir missing: {data_dir}", dedupe_key="backup-data-dir")
        return 1

    # Date-partitioned R2 key must match operator-perceived Taipei date — cron fires at
    # 04:00 Asia/Taipei (= 20:00 UTC previous day), so datetime.now(timezone.utc) would
    # stamp backups with yesterday's folder. Precedent: PR #67 for Robin pubmed_digest.
    now = datetime.now(ZoneInfo("Asia/Taipei"))
    # `... or "30"` handles both unset (None) and empty ("=" in .env) — empty string
    # passes the os.environ.get default-arg check and would crash int("") otherwise.
    retention_days = int(os.environ.get("NAKAMA_BACKUP_RETENTION_DAYS") or "30")

    failures: list[str] = []
    for db_name in _DBS:
        ok = _backup_one(data_dir, db_name, client, now)
        if not ok:
            failures.append(db_name)

    if failures:
        msg = f"backup failed dbs={failures}"
        logger.error(msg)
        record_failure(_JOB_NAME, msg)
        alert("error", "backup", msg, dedupe_key="backup-upload-fail")
        return 1

    # Retention runs only after all uploads succeeded, so a failed day never
    # eats into the previous-day snapshot window.
    for db_name in _DBS:
        stem = db_name.removesuffix(".db")
        try:
            deleted = client.delete_older_than(retention_days, prefix=f"{stem}/")
            if deleted:
                logger.info(
                    "retention db=%s deleted=%d keep_days=%d",
                    db_name,
                    deleted,
                    retention_days,
                )
        except R2Unavailable as exc:
            # Retention failure is a warning — the fresh upload already succeeded.
            logger.warning("retention prune failed db=%s err=%s", db_name, exc)

    logger.info("nakama backup complete dbs=%d", len(_DBS))
    record_success(_JOB_NAME)
    return 0


if __name__ == "__main__":
    sys.exit(main())
