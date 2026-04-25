"""Mirror previous-day's R2 nakama-backup snapshots to Backblaze B2 (secondary).

Runs daily at 04:30 Asia/Taipei — 30 minutes after the primary 04:00 backup
write, so today's snapshot is always present in R2 by the time we mirror it.
The window choice trades off "freshness" against "primary backup definitely
done" — pushing earlier risks missing a slow upload, pushing later eats into
the morning operation window.

Mirror posture:
- Source: R2 `nakama-backup` (read via `R2Client.from_nakama_backup_env`)
- Sink:   B2 `B2_BUCKET_NAME` (via `B2Client.from_env`)
- Same key on both sides — restore tooling can swap endpoint without
  rewriting paths.
- Idempotent: B2 head_object check skips already-mirrored keys. Re-runs
  during the day are safe and cheap.

Cron:
    30 4 * * *  cd /home/nakama && /usr/bin/python3 scripts/mirror_backup_to_secondary.py \\
        >> /var/log/nakama/backup-mirror.log 2>&1

Env (all optional):
    NAKAMA_MIRROR_TIERS  comma-separated tier names to mirror; default "daily,weekly,monthly"
                         set to "daily" if you only want vendor-redundancy on the freshest tier
    NAKAMA_MIRROR_LOOKBACK_DAYS  how far back to scan for un-mirrored keys, default 7

Exit codes:
    0 — every applicable key mirrored or already present
    1 — at least one mirror upload failed; primary R2 copy unaffected
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from shared.config import load_config
from shared.log import get_logger
from shared.r2_client import R2Client, R2Unavailable
from shared.secondary_storage import B2Client, B2Unavailable

logger = get_logger("nakama.backup_mirror")

_DBS = ("state", "nakama")
_ALL_TIERS = (
    ("daily", ""),
    ("weekly", "-weekly"),
    ("monthly", "-monthly"),
)


def _enabled_tiers() -> list[tuple[str, str]]:
    raw = os.environ.get("NAKAMA_MIRROR_TIERS") or "daily,weekly,monthly"
    wanted = {t.strip() for t in raw.split(",") if t.strip()}
    return [(name, suffix) for (name, suffix) in _ALL_TIERS if name in wanted]


def _already_mirrored(b2: B2Client, key: str) -> bool:
    """Return True if the key already exists in B2 — skip re-upload."""
    try:
        b2.head_object(key)
        return True
    except B2Unavailable:
        # head raises on 404; treat as "not mirrored yet" rather than parsing
        # the underlying ClientError code (B2 returns NoSuchKey / 404 / others
        # depending on auth + region). Safer to attempt re-upload than skip.
        return False


def _mirror_one(r2: R2Client, b2: B2Client, key: str) -> bool:
    """Download from R2 → upload to B2. Returns True on success."""
    if _already_mirrored(b2, key):
        logger.info("skip key=%s reason=already_mirrored", key)
        return True

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        local = tmp / Path(key).name
        try:
            r2._s3.download_file(  # noqa: SLF001
                Bucket=r2.bucket,
                Key=key,
                Filename=str(local),
            )
        except Exception as exc:
            logger.error("r2 download failed key=%s err=%s", key, exc)
            return False

        try:
            b2.upload_file(local, key, content_type="application/gzip")
        except B2Unavailable as exc:
            logger.error("b2 upload failed key=%s err=%s", key, exc)
            return False
    return True


def main() -> int:
    load_config()

    try:
        r2 = R2Client.from_nakama_backup_env()
    except R2Unavailable as exc:
        logger.error("r2 source unavailable: %s", exc)
        return 1

    try:
        b2 = B2Client.from_env()
    except B2Unavailable as exc:
        # B2 not configured → log and exit clean (no failure, nothing to do).
        # This lets the cron be installed on a VPS that hasn't yet had B2 set up
        # without erroring every night until env arrives.
        logger.warning("b2 sink not configured: %s — exiting clean (no mirror)", exc)
        return 0

    failures: list[str] = []
    mirrored: list[str] = []
    skipped: list[str] = []

    for db in _DBS:
        for tier_name, suffix in _enabled_tiers():
            prefix = f"{db}{suffix}/"
            try:
                snaps = r2.list_objects(prefix=prefix, max_keys=1000)
            except R2Unavailable as exc:
                logger.error("r2 list failed prefix=%s err=%s", prefix, exc)
                failures.append(prefix)
                continue
            # Process newest first so a partial run still mirrors the most-recent
            snaps.sort(key=lambda o: o.last_modified, reverse=True)
            for snap in snaps:
                # head_object check inside _mirror_one; track skip vs new
                if _already_mirrored(b2, snap.key):
                    skipped.append(snap.key)
                    continue
                ok = _mirror_one(r2, b2, snap.key)
                if ok:
                    mirrored.append(snap.key)
                else:
                    failures.append(snap.key)

    logger.info(
        "mirror complete mirrored=%d skipped=%d failed=%d",
        len(mirrored),
        len(skipped),
        len(failures),
    )
    if failures:
        for k in failures:
            logger.error("mirror failed key=%s", k)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
