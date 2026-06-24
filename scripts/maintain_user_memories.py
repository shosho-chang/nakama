"""Schedule maintenance for ``user_memories`` (Nami / Zoro / Robin agent SQLite store).

Closes a real gap: ``shared.agent_memory`` exposes ``decay()`` and ``prune()`` but
nothing was calling them. Without decay the ``confidence × recency`` ranking
degenerates — every memory stays at confidence 1.0 forever and rank becomes
recency-only. Without prune low-confidence rows accumulate indefinitely.

Modes:
  decay  — multiply confidence by 0.9 for rows untouched >30 days
  prune  — delete rows with confidence < 0.1

Cron (Asia/Taipei):
  # Decay weekly — Mon 04:15
  15 4 * * 1  cd /home/nakama && /usr/bin/python3 scripts/maintain_user_memories.py decay \\
      >> /var/log/nakama/user-memories-decay.log 2>&1

  # Prune monthly — 1st 04:30
  30 4 1 * *  cd /home/nakama && /usr/bin/python3 scripts/maintain_user_memories.py prune \\
      >> /var/log/nakama/user-memories-prune.log 2>&1

Idempotent: re-running either mode is safe.

Usage:
  python scripts/maintain_user_memories.py decay              # apply
  python scripts/maintain_user_memories.py decay --dry-run    # report only
  python scripts/maintain_user_memories.py prune --threshold 0.2

Exit codes:
  0 — success
  2 — invalid argument
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from shared import agent_memory
from shared.log import get_logger

logger = get_logger("nakama.maintain_user_memories")


def _count_below(threshold: float) -> int:
    """How many rows would prune() delete at the given threshold."""
    agent_memory._ensure_schema()
    from shared.state import _get_conn

    conn = _get_conn()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM user_memories WHERE confidence < ?",
        (threshold,),
    ).fetchone()
    return int(row["n"])


def _count_stale(older_than_days: int) -> int:
    """How many rows would decay() touch."""
    agent_memory._ensure_schema()
    from datetime import timedelta

    from shared.state import _get_conn

    conn = _get_conn()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM user_memories WHERE last_accessed_at < ?",
        (cutoff,),
    ).fetchone()
    return int(row["n"])


def cmd_decay(args: argparse.Namespace) -> int:
    candidates = _count_stale(args.older_than_days)
    if args.dry_run:
        logger.info(
            "decay dry-run older_than_days=%d factor=%.2f candidates=%d",
            args.older_than_days,
            args.factor,
            candidates,
        )
        print(
            f"DRY-RUN decay: would touch {candidates} rows "
            f"(older_than_days={args.older_than_days}, factor={args.factor})"
        )
        return 0

    affected = agent_memory.decay(older_than_days=args.older_than_days, factor=args.factor)
    logger.info(
        "decay applied older_than_days=%d factor=%.2f affected=%d",
        args.older_than_days,
        args.factor,
        affected,
    )
    print(f"decay: {affected} rows multiplied by {args.factor}")
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    candidates = _count_below(args.threshold)
    if args.dry_run:
        logger.info(
            "prune dry-run threshold=%.2f candidates=%d",
            args.threshold,
            candidates,
        )
        print(f"DRY-RUN prune: would delete {candidates} rows (threshold={args.threshold})")
        return 0

    deleted = agent_memory.prune(confidence_threshold=args.threshold)
    logger.info("prune applied threshold=%.2f deleted=%d", args.threshold, deleted)
    print(f"prune: deleted {deleted} rows below confidence {args.threshold}")
    return 0


def cmd_episodic_forget(args: argparse.Namespace) -> int:
    from shared import episodic_memory

    if args.dry_run:
        from datetime import timedelta

        from shared.state import _get_conn

        episodic_memory._ensure_schema()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=args.older_than_days)).isoformat()
        n = (
            _get_conn()
            .execute(
                "SELECT COUNT(*) AS n FROM episodic_memories "
                "WHERE invalidated_at IS NULL AND occurred_at < ?",
                (cutoff,),
            )
            .fetchone()["n"]
        )
        print(f"DRY-RUN episodic-forget: would invalidate {n} events (>{args.older_than_days}d)")
        return 0

    affected = episodic_memory.forget_older_than(days=args.older_than_days)
    logger.info(
        "episodic-forget applied older_than_days=%d affected=%d", args.older_than_days, affected
    )
    print(f"episodic-forget: invalidated {affected} events older than {args.older_than_days}d")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="maintain_user_memories")
    sub = parser.add_subparsers(dest="mode", required=True)

    p_decay = sub.add_parser("decay", help="multiply confidence of stale rows")
    p_decay.add_argument("--older-than-days", type=int, default=30)
    p_decay.add_argument("--factor", type=float, default=0.9)
    p_decay.add_argument("--dry-run", action="store_true")
    p_decay.set_defaults(func=cmd_decay)

    p_prune = sub.add_parser("prune", help="delete rows below confidence threshold")
    p_prune.add_argument("--threshold", type=float, default=0.1)
    p_prune.add_argument("--dry-run", action="store_true")
    p_prune.set_defaults(func=cmd_prune)

    p_epi = sub.add_parser(
        "episodic-forget", help="soft-invalidate old un-promoted episodic events"
    )
    p_epi.add_argument("--older-than-days", type=int, default=60)
    p_epi.add_argument("--dry-run", action="store_true")
    p_epi.set_defaults(func=cmd_episodic_forget)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
