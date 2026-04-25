"""Daily snapshot of Nakama state DBs into the R2 nakama-backup bucket.

Runs every day at 04:00 Asia/Taipei. The same gzip is uploaded to up to three
tier-specific prefixes, each with its own retention window:

- daily   (always): `<stem>/YYYY/MM/DD/<db>.db.gz`              retention 30d
- weekly  (Sundays): `<stem>-weekly/YYYY-WNN/<db>.db.gz`        retention 12w
- monthly (1st of month): `<stem>-monthly/YYYY-MM/<db>.db.gz`   retention 12m

The 3-tier layout means a single 24h RPO window doesn't ratchet down further
when ops accumulate — older recovery points (last week, last quarter, last
year) survive even after daily 30d window has rolled over.

Snapshot uses SQLite's online `.backup` API for atomic, non-blocking copies.
Retention runs only after successful upload, so a failed day never eats into
the previous-day snapshot window.

Cron (unchanged):
    0 4 * * *  cd /home/nakama && python3 scripts/backup_nakama_state.py \\
        >> /var/log/nakama/nakama-backup.log 2>&1

Env (all retention vars optional):
    NAKAMA_BACKUP_RETENTION_DAYS              daily tier, default 30
    NAKAMA_BACKUP_WEEKLY_RETENTION_WEEKS      weekly tier, default 12
    NAKAMA_BACKUP_MONTHLY_RETENTION_MONTHS    monthly tier, default 12

Exit codes:
    0 — all configured DBs uploaded (or gracefully skipped if missing/empty)
    1 — at least one DB / upload failed; check log, preserve service, try again next tick
"""

from __future__ import annotations

import gzip
import os
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from shared.config import load_config
from shared.log import get_logger
from shared.r2_client import R2Client, R2Unavailable

logger = get_logger("nakama.backup")

# `nakama.db` is currently unused (0 bytes) but lives alongside state.db and was in
# the original Phase 1 layout; back it up too so future use doesn't silently bypass.
_DBS = ("state.db", "nakama.db")


# ---- tier model -------------------------------------------------------------


@dataclass(frozen=True)
class _TierWrite:
    """One tier's write target + retention. Built once per backup run."""

    name: str  # "daily" / "weekly" / "monthly"
    prefix: str  # "state/" / "state-weekly/" / "state-monthly/"
    key: str  # full R2 key for today's upload
    retention_days: int  # threshold for delete_older_than


def _build_tier_writes(stem: str, db_name: str, now: datetime) -> list[_TierWrite]:
    """Return the tier writes that apply to `db_name` on `now`.

    Daily is always present. Weekly is added on Sundays (weekday == 6). Monthly
    is added on the 1st of the month. On a Sunday-1st, all three are written.
    """
    # `... or "<default>"` handles both unset (None) and explicitly-empty
    # ("NAKAMA_BACKUP_RETENTION_DAYS=" in .env) — the latter would crash the
    # int() cast since "" passes os.environ.get's default-arg check.
    daily_days = int(os.environ.get("NAKAMA_BACKUP_RETENTION_DAYS") or "30")
    weekly_weeks = int(os.environ.get("NAKAMA_BACKUP_WEEKLY_RETENTION_WEEKS") or "12")
    monthly_months = int(os.environ.get("NAKAMA_BACKUP_MONTHLY_RETENTION_MONTHS") or "12")

    writes: list[_TierWrite] = [
        _TierWrite(
            name="daily",
            prefix=f"{stem}/",
            key=f"{stem}/{now:%Y/%m/%d}/{db_name}.gz",
            retention_days=daily_days,
        )
    ]
    if now.weekday() == 6:  # Sunday in Python's Mon=0..Sun=6
        writes.append(
            _TierWrite(
                name="weekly",
                prefix=f"{stem}-weekly/",
                # %G %V = ISO year + ISO week (handles year-boundary edge cases that %Y%U won't)
                key=f"{stem}-weekly/{now:%G-W%V}/{db_name}.gz",
                retention_days=weekly_weeks * 7,
            )
        )
    if now.day == 1:
        writes.append(
            _TierWrite(
                name="monthly",
                prefix=f"{stem}-monthly/",
                key=f"{stem}-monthly/{now:%Y-%m}/{db_name}.gz",
                # 31-day cushion per month so a 30-day-month rollover doesn't accidentally
                # delete this month's snapshot before the next one writes.
                retention_days=monthly_months * 31,
            )
        )
    return writes


# ---- per-DB pipeline --------------------------------------------------------


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
    """Snapshot + gzip once, upload to all applicable tier prefixes.

    Returns True on success (uploaded or gracefully skipped); False on real failure.
    """
    src = data_dir / db_name
    if not src.exists():
        logger.info("skip db=%s reason=not_found", db_name)
        return True
    if src.stat().st_size == 0:
        logger.info("skip db=%s reason=empty", db_name)
        return True

    stem = db_name.removesuffix(".db")  # "state" / "nakama"
    tier_writes = _build_tier_writes(stem, db_name, now)

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
            "db=%s snapshot=%d gz=%d ratio=%.2fx tiers=%s",
            db_name,
            snapshot.stat().st_size,
            size_bytes,
            snapshot.stat().st_size / max(size_bytes, 1),
            ",".join(t.name for t in tier_writes),
        )

        for write in tier_writes:
            try:
                client.upload_file(gz, write.key, content_type="application/gzip")
            except R2Unavailable as exc:
                logger.error(
                    "upload failed db=%s tier=%s key=%s err=%s",
                    db_name,
                    write.name,
                    write.key,
                    exc,
                )
                return False

    return True


def _prune_for_db(client: R2Client, db_name: str, now: datetime) -> None:
    """Run retention pruning across all tiers for `db_name`.

    Each tier's prune is independent — failure in one logs a warning but
    doesn't abort the others.
    """
    stem = db_name.removesuffix(".db")
    # Always prune all 3 tiers (not just today's writes), so a tier that wrote
    # last Sunday still gets its retention window enforced today.
    all_tier_prefixes = [
        ("daily", f"{stem}/", int(os.environ.get("NAKAMA_BACKUP_RETENTION_DAYS") or "30")),
        (
            "weekly",
            f"{stem}-weekly/",
            int(os.environ.get("NAKAMA_BACKUP_WEEKLY_RETENTION_WEEKS") or "12") * 7,
        ),
        (
            "monthly",
            f"{stem}-monthly/",
            int(os.environ.get("NAKAMA_BACKUP_MONTHLY_RETENTION_MONTHS") or "12") * 31,
        ),
    ]
    for tier_name, prefix, retention_days in all_tier_prefixes:
        try:
            deleted = client.delete_older_than(retention_days, prefix=prefix)
            if deleted:
                logger.info(
                    "retention db=%s tier=%s deleted=%d keep_days=%d",
                    db_name,
                    tier_name,
                    deleted,
                    retention_days,
                )
        except R2Unavailable as exc:
            # Retention failure is a warning — the fresh upload already succeeded.
            logger.warning("retention prune failed db=%s tier=%s err=%s", db_name, tier_name, exc)


def main() -> int:
    load_config()

    try:
        client = R2Client.from_nakama_backup_env(mode="write")
    except R2Unavailable as exc:
        logger.error("r2 client unavailable: %s", exc)
        return 1

    data_dir = Path(os.environ.get("NAKAMA_DATA_DIR", "/home/nakama/data"))
    if not data_dir.is_dir():
        logger.error("data dir missing: %s", data_dir)
        return 1

    # Date-partitioned R2 key must match operator-perceived Taipei date — cron fires at
    # 04:00 Asia/Taipei (= 20:00 UTC previous day), so datetime.now(timezone.utc) would
    # stamp backups with yesterday's folder. Precedent: PR #67 for Robin pubmed_digest.
    now = datetime.now(ZoneInfo("Asia/Taipei"))

    failures: list[str] = []
    for db_name in _DBS:
        ok = _backup_one(data_dir, db_name, client, now)
        if not ok:
            failures.append(db_name)

    if failures:
        logger.error("backup failed dbs=%s", failures)
        return 1

    # Retention runs only after all uploads succeeded, so a failed day never
    # eats into any tier's snapshot window.
    for db_name in _DBS:
        _prune_for_db(client, db_name, now)

    logger.info("nakama backup complete dbs=%d", len(_DBS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
