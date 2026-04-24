"""Zoro CLI entrypoint.

Usage:
    python -m agents.zoro                    # backward-compat cron：scout 一次（publish 真的送）
    python -m agents.zoro scout              # 同上，明確形式
    python -m agents.zoro scout --dry-run    # 跑 pipeline 但不 publish、不 record

Cron 裡掛 `python -m agents.zoro scout`（見 cron.conf）— legacy 無參數路徑保留給
還沒更新 cron 的 VPS，避免 deploy 錯序時 cron 變 NotImplementedError。
"""

from __future__ import annotations

import argparse
import json
import sys


def _cmd_scout(args: argparse.Namespace) -> int:
    from agents.zoro.brainstorm_scout import run

    best = run(publish=not args.dry_run, record=not args.dry_run)
    summary = {
        "picked": best.title if best else None,
        "velocity": round(best.velocity_score, 2) if best else None,
        "relevance": round(best.relevance_score, 2) if best else None,
        "domain": best.domain if best else None,
        "dry_run": args.dry_run,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agents.zoro", description="Zoro scout agent")
    sub = parser.add_subparsers(dest="command")

    scout = sub.add_parser("scout", help="Run brainstorm scout (1 tick)")
    scout.add_argument(
        "--dry-run",
        action="store_true",
        help="Run pipeline but don't publish to Slack or record to pushed_topics",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    from shared.config import load_config

    load_config()

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "scout":
        return _cmd_scout(args)

    # Legacy fallback: no subcommand → scout (publish 真送)
    return _cmd_scout(argparse.Namespace(dry_run=False))


if __name__ == "__main__":
    sys.exit(main())
