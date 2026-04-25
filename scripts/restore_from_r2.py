"""Restore Nakama state DBs from the R2 nakama-backup bucket.

Companion to `scripts/backup_nakama_state.py`. Two subcommands:

  list      — list most-recent snapshots for a given DB
  restore   — fetch a snapshot, gunzip, verify schema; optionally replace target

Default mode of `restore` is dry-run: download to `/tmp/`, run integrity checks,
print a report. Pass `--apply` to overwrite the live DB (with auto-backup of
the pre-existing file as `<target>.pre-restore.<ts>`).

The script reuses `R2Client.from_nakama_backup_env()` so it consumes the same
env vars as backup. See `docs/runbooks/disaster-recovery.md` for the full DR
playbook this script is part of.

Usage:
    # list the 5 latest state.db snapshots
    python scripts/restore_from_r2.py list --db state --limit 5

    # dry-run: fetch latest, verify schema, print report (no live DB touched)
    python scripts/restore_from_r2.py restore --db state

    # dry-run with explicit date
    python scripts/restore_from_r2.py restore --db state --date 2026-04-23

    # apply: replace /home/nakama/data/state.db with the snapshot
    python scripts/restore_from_r2.py restore --db state --apply

Exit codes:
    0 — operation succeeded (or dry-run report printed)
    1 — R2 unavailable, snapshot missing, integrity check failed, or apply blocked
"""

from __future__ import annotations

import argparse
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
from shared.r2_client import R2Client, R2Object, R2Unavailable

logger = get_logger("nakama.restore")

_VALID_DBS = ("state", "nakama")
_DEFAULT_TARGET_DIR = Path(os.environ.get("NAKAMA_DATA_DIR", "/home/nakama/data"))


@dataclass(frozen=True)
class RestoreReport:
    """Result of a restore (dry-run or apply). Pretty-printed at end of run."""

    db: str
    snapshot_key: str
    snapshot_size: int
    decompressed_size: int
    integrity_ok: bool
    table_count: int
    row_count_total: int
    target_path: Path
    applied: bool
    pre_restore_backup: Path | None


# ---- snapshot listing -------------------------------------------------------


def list_snapshots(client: R2Client, db: str, limit: int = 10) -> list[R2Object]:
    """Return the `limit` most-recent snapshots for `db`, newest first."""
    objs = client.list_objects(prefix=f"{db}/", max_keys=1000)
    objs.sort(key=lambda o: o.last_modified, reverse=True)
    return objs[:limit]


def find_snapshot(client: R2Client, db: str, date: str | None) -> R2Object:
    """Locate the snapshot for `date` (YYYY-MM-DD) or the latest if `date` is None."""
    if date is not None:
        # Validate format: ValueError surfaces clearly if user typo'd
        parsed = datetime.strptime(date, "%Y-%m-%d")
        key = f"{db}/{parsed:%Y/%m/%d}/{db}.db.gz"
        try:
            return client.head_object(key)
        except R2Unavailable as exc:
            raise SystemExit(f"snapshot not found: {key} ({exc})") from exc

    snaps = list_snapshots(client, db, limit=1)
    if not snaps:
        raise SystemExit(f"no snapshots in bucket for db={db}")
    return snaps[0]


# ---- fetch + verify ---------------------------------------------------------


def fetch_to_temp(client: R2Client, snap: R2Object, work_dir: Path) -> Path:
    """Download snap.gz to `work_dir`, gunzip in-place, return path to .db file."""
    gz_path = work_dir / f"{Path(snap.key).name}"
    db_path = work_dir / Path(snap.key).name.removesuffix(".gz")

    # boto3 download_file via R2Client — wrap the s3 client directly
    try:
        client._s3.download_file(  # noqa: SLF001 — direct s3 reuse, R2Client doesn't expose download yet
            Bucket=client.bucket,
            Key=snap.key,
            Filename=str(gz_path),
        )
    except Exception as exc:  # boto3 raises ClientError / BotoCoreError
        raise R2Unavailable(f"download_file key={snap.key} failed: {exc}") from exc

    with gzip.open(gz_path, "rb") as f_in, open(db_path, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)

    logger.info(
        "fetched key=%s gz=%d db=%d",
        snap.key,
        gz_path.stat().st_size,
        db_path.stat().st_size,
    )
    return db_path


def verify_db(db_path: Path) -> tuple[bool, int, int]:
    """Run `PRAGMA integrity_check` + count tables + sum rows.

    Returns (integrity_ok, table_count, row_count_total). A 0-byte sentinel
    file (legitimate for nakama.db today) returns (True, 0, 0). Any sqlite
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
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
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


# ---- apply (overwrite target) -----------------------------------------------


def apply_to_target(restored: Path, target: Path, taipei_now: datetime) -> Path | None:
    """Move `restored` to `target`. Pre-existing `target` is preserved as
    `<target>.pre-restore.<YYYYMMDD_HHMMSS>` (Asia/Taipei timestamp).

    Returns path of the pre-restore backup, or None if target didn't exist.
    """
    backup_path: Path | None = None
    if target.exists():
        backup_path = target.with_suffix(target.suffix + f".pre-restore.{taipei_now:%Y%m%d_%H%M%S}")
        shutil.move(str(target), str(backup_path))
        logger.info("preserved existing target=%s as=%s", target, backup_path)

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(restored), str(target))
    logger.info("restored target=%s size=%d", target, target.stat().st_size)
    return backup_path


# ---- CLI orchestration ------------------------------------------------------


def cmd_list(args: argparse.Namespace, client: R2Client) -> int:
    snaps = list_snapshots(client, args.db, limit=args.limit)
    if not snaps:
        print(f"(no snapshots for db={args.db})")
        return 0
    print(f"{'KEY':<48} {'LAST MODIFIED (UTC)':<26} {'SIZE':>10}")
    for s in snaps:
        print(f"{s.key:<48} {s.last_modified.isoformat():<26} {s.size:>10}")
    return 0


def cmd_restore(args: argparse.Namespace, client: R2Client) -> int:
    snap = find_snapshot(client, args.db, args.date)
    target = Path(args.target) if args.target else _DEFAULT_TARGET_DIR / f"{args.db}.db"
    taipei_now = datetime.now(ZoneInfo("Asia/Taipei"))

    if target.exists() and not args.apply:
        # Dry-run never writes to a path that already exists — surfaces the
        # "you typed the wrong target" mistake before any damage.
        logger.info("target exists and --apply not set; dry-run will use temp path")

    with tempfile.TemporaryDirectory() as tmp_str:
        work = Path(tmp_str)
        restored = fetch_to_temp(client, snap, work)
        ok, n_tables, n_rows = verify_db(restored)

        if not ok:
            logger.error("integrity check FAILED for %s", snap.key)
            return 1

        applied = False
        backup_path: Path | None = None

        if args.apply:
            # Move out of TemporaryDirectory before context closes
            applied_target_dir = target.parent
            applied_target_dir.mkdir(parents=True, exist_ok=True)
            staged = applied_target_dir / f"{target.name}.staged.{taipei_now:%Y%m%d_%H%M%S}"
            shutil.copy2(str(restored), str(staged))
            backup_path = apply_to_target(staged, target, taipei_now)
            applied = True

        report = RestoreReport(
            db=args.db,
            snapshot_key=snap.key,
            snapshot_size=snap.size,
            decompressed_size=restored.stat().st_size if restored.exists() else 0,
            integrity_ok=ok,
            table_count=n_tables,
            row_count_total=n_rows,
            target_path=target,
            applied=applied,
            pre_restore_backup=backup_path,
        )
        _print_report(report)

    return 0


def _print_report(r: RestoreReport) -> None:
    print()
    print("=" * 60)
    print(f"  Restore report — db={r.db}")
    print("=" * 60)
    print(f"  Snapshot         {r.snapshot_key}")
    print(f"  Snapshot size    {r.snapshot_size} bytes (gz)")
    print(f"  Decompressed     {r.decompressed_size} bytes")
    print(f"  Integrity check  {'OK' if r.integrity_ok else 'FAIL'}")
    print(f"  Tables           {r.table_count}")
    print(f"  Rows (sum)       {r.row_count_total}")
    print(f"  Target           {r.target_path}")
    print(f"  Mode             {'APPLIED' if r.applied else 'DRY-RUN (no live DB touched)'}")
    if r.pre_restore_backup:
        print(f"  Pre-restore kept {r.pre_restore_backup}")
    print("=" * 60)
    print()


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="restore_from_r2",
        description="Restore Nakama state DBs from R2 nakama-backup bucket.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="list recent snapshots")
    p_list.add_argument("--db", choices=_VALID_DBS, default="state")
    p_list.add_argument("--limit", type=int, default=10)

    p_restore = sub.add_parser("restore", help="fetch + verify (dry-run by default)")
    p_restore.add_argument("--db", choices=_VALID_DBS, required=True)
    p_restore.add_argument("--date", help="YYYY-MM-DD; default = latest available")
    p_restore.add_argument(
        "--target",
        help=f"override target path (default: {_DEFAULT_TARGET_DIR}/<db>.db)",
    )
    p_restore.add_argument(
        "--apply",
        action="store_true",
        help="replace target (with auto-backup of pre-existing as .pre-restore.<ts>)",
    )

    return p


def main(argv: list[str] | None = None) -> int:
    load_config()
    args = _build_parser().parse_args(argv)

    try:
        client = R2Client.from_nakama_backup_env()
    except R2Unavailable as exc:
        logger.error("r2 client unavailable: %s", exc)
        return 1

    if args.cmd == "list":
        return cmd_list(args, client)
    if args.cmd == "restore":
        try:
            return cmd_restore(args, client)
        except R2Unavailable as exc:
            logger.error("restore failed: %s", exc)
            return 1
    raise SystemExit(f"unknown command: {args.cmd}")


if __name__ == "__main__":
    sys.exit(main())
