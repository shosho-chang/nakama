"""Weekly integrity verification of R2 backup snapshots.

Runs every Sunday 03:30 Asia/Taipei (before the 04:00 daily backup write,
so today's verification doesn't accidentally clear today's snapshot from
the rolling window check). For each (db, tier) pair, downloads the most
recent N snapshots, runs `PRAGMA integrity_check` + sums row counts, and
reports a verdict.

What "verifying" catches that the daily backup script doesn't:
- gz file truncated mid-upload (R2 thinks it succeeded; gunzip fails)
- sqlite snapshot corrupted between snapshot + gzip step
- multi-week silent rot (cosmic ray flips bit, R2 doesn't repair until read)

Cron:
    30 3 * * 0  cd /home/nakama && /usr/bin/python3 scripts/verify_backup_integrity.py \\
        >> /var/log/nakama/backup-integrity.log 2>&1

Env (all optional):
    NAKAMA_INTEGRITY_DAILY_SAMPLES   verify last N daily snapshots, default 7
    NAKAMA_INTEGRITY_WEEKLY_SAMPLES  verify last N weekly snapshots, default 4
    NAKAMA_INTEGRITY_MONTHLY_SAMPLES verify last N monthly snapshots, default 3

Exit codes:
    0 — every snapshot verified passed integrity_check
    1 — at least one snapshot failed; check log for keys + alert manually
"""

from __future__ import annotations

import gzip
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from shared.alerts import alert
from shared.config import load_config
from shared.heartbeat import record_failure, record_success
from shared.log import get_logger
from shared.r2_client import R2Client, R2Object, R2Unavailable
from shared.sqlite_integrity import verify_db

logger = get_logger("nakama.backup_integrity")

_JOB_NAME = "nakama-backup-integrity"  # heartbeat key — keep stable across releases
_DBS = ("state", "nakama")
# Tier prefix layout: keep in sync with `scripts/backup_nakama_state.py`.
# Phase 2A introduces -weekly / -monthly prefixes; this script verifies
# whichever exist (no error if the bucket is still all-daily).
_TIERS = (
    ("daily", ""),
    ("weekly", "-weekly"),
    ("monthly", "-monthly"),
)


@dataclass(frozen=True)
class IntegrityResult:
    """One verified-snapshot record for the run summary."""

    key: str
    tier: str
    db: str
    integrity_ok: bool
    table_count: int
    row_count: int
    error: str | None = None


def _samples_for_tier(tier_name: str) -> int:
    env_var = f"NAKAMA_INTEGRITY_{tier_name.upper()}_SAMPLES"
    default = {"daily": 7, "weekly": 4, "monthly": 3}[tier_name]
    return int(os.environ.get(env_var) or str(default))


def _verify_one(client: R2Client, snap: R2Object, tier: str, db: str) -> IntegrityResult:
    """Download → gunzip → verify_db → IntegrityResult."""
    with tempfile.TemporaryDirectory() as tmp_str:
        work = Path(tmp_str)
        gz_path = work / Path(snap.key).name
        db_path = work / Path(snap.key).name.removesuffix(".gz")

        try:
            client._s3.download_file(  # noqa: SLF001 — same direct reuse as restore_from_r2.py
                Bucket=client.bucket,
                Key=snap.key,
                Filename=str(gz_path),
            )
        except Exception as exc:
            return IntegrityResult(snap.key, tier, db, False, 0, 0, f"download failed: {exc}")

        try:
            with gzip.open(gz_path, "rb") as f_in, open(db_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        except (OSError, EOFError) as exc:
            return IntegrityResult(snap.key, tier, db, False, 0, 0, f"gunzip failed: {exc}")

        ok, n_tables, n_rows = verify_db(db_path)
        return IntegrityResult(
            snap.key,
            tier,
            db,
            ok,
            n_tables,
            n_rows,
            None if ok else "integrity_check failed",
        )


def main() -> int:
    load_config()

    try:
        # Verifier only reads — least-privilege via mode="read".
        client = R2Client.from_nakama_backup_env(mode="read")
    except R2Unavailable as exc:
        msg = f"r2 client unavailable: {exc}"
        logger.error(msg)
        record_failure(_JOB_NAME, msg)
        alert(
            "error",
            "backup",
            f"backup-integrity: {msg}",
            dedupe_key="backup-integrity-r2-unavailable",
        )
        return 1

    results: list[IntegrityResult] = []
    for db in _DBS:
        for tier_name, suffix in _TIERS:
            prefix = f"{db}{suffix}/"
            n = _samples_for_tier(tier_name)
            try:
                snaps = client.list_objects(prefix=prefix, max_keys=1000)
            except R2Unavailable as exc:
                logger.error("list failed prefix=%s err=%s", prefix, exc)
                results.append(IntegrityResult(prefix, tier_name, db, False, 0, 0, str(exc)))
                continue
            # Newest first, then take N
            snaps.sort(key=lambda o: o.last_modified, reverse=True)
            for snap in snaps[:n]:
                result = _verify_one(client, snap, tier_name, db)
                results.append(result)
                _log_result(result)

    failures = [r for r in results if not r.integrity_ok]
    logger.info(
        "integrity verification complete checked=%d ok=%d fail=%d",
        len(results),
        len(results) - len(failures),
        len(failures),
    )

    if failures:
        for f in failures:
            logger.error(
                "integrity FAILED key=%s tier=%s db=%s err=%s",
                f.key,
                f.tier,
                f.db,
                f.error,
            )
        msg = f"backup integrity failed count={len(failures)} of {len(results)}"
        record_failure(_JOB_NAME, msg)
        alert("error", "backup", f"integrity: {msg}", dedupe_key="backup-integrity-fail")
        return 1

    record_success(_JOB_NAME)
    return 0


def _log_result(r: IntegrityResult) -> None:
    if r.integrity_ok:
        logger.info(
            "verify ok key=%s tables=%d rows=%d",
            r.key,
            r.table_count,
            r.row_count,
        )
    else:
        logger.error("verify FAIL key=%s err=%s", r.key, r.error)


if __name__ == "__main__":
    sys.exit(main())
