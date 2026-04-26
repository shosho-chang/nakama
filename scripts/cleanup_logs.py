"""Daily retention sweep over `data/logs.db` — Phase 5C.

Deletes log rows older than `--older-than-days` (default 30), then VACUUMs
the DB to reclaim disk. Log volume is bounded so this stays cheap (a few
MB/day → < 200MB at steady state).

Cron entry (added in this PR):

    0 4 * * *  cd /home/nakama && /usr/bin/python3 scripts/cleanup_logs.py \\
        >> /var/log/nakama/cleanup-logs.log 2>&1

Heartbeat: `nakama-cleanup-logs` (registered in CRON_SCHEDULES). Daily 04:00
Asia/Taipei, 60min grace.

Exit codes:
    0 — sweep + VACUUM succeeded (or DB had nothing to delete)
    1 — sweep raised; logs still readable but disk may grow until fixed
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta

from shared.config import load_config
from shared.heartbeat import record_failure, record_success
from shared.log import get_logger
from shared.log_index import LogIndex

logger = get_logger("nakama.cleanup_logs")

_JOB_NAME = "nakama-cleanup-logs"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--older-than-days",
        type=int,
        default=30,
        help="Delete rows whose ts is older than now() - N days (default: 30)",
    )
    parser.add_argument(
        "--no-vacuum",
        action="store_true",
        help="Skip VACUUM after delete (saves ~100ms; disk not reclaimed until next run)",
    )
    args = parser.parse_args()

    load_config()

    try:
        idx = LogIndex.from_default_path()
        deleted = idx.cleanup(older_than=timedelta(days=args.older_than_days))
        if not args.no_vacuum:
            idx.vacuum()
        logger.info(
            "log cleanup ok",
            extra={
                "job": _JOB_NAME,
                "deleted": deleted,
                "older_than_days": args.older_than_days,
                "vacuumed": not args.no_vacuum,
            },
        )
        record_success(_JOB_NAME)
        return 0
    except Exception as exc:  # noqa: BLE001 — cron entry top-level catch
        logger.exception("log cleanup failed")
        record_failure(_JOB_NAME, str(exc)[:500])
        return 1


if __name__ == "__main__":
    sys.exit(main())
