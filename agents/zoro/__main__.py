"""Zoro CLI entrypoint.

Usage:
    python -m agents.zoro scout              # 跑 scout 一次（publish + record 真的送）
    python -m agents.zoro scout --dry-run    # 跑 pipeline 但不 publish、不 record

無 subcommand → print help 並 exit 2，**不** fallback 到 scout — 避免 CI / devbox
誤打 `python -m agents.zoro` 就把訊息真的貼到 #brainstorm（code review N2 修正）。
Cron 必須明確寫 `scout` subcommand（cron.conf 已這樣設）。
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

    # 無 subcommand → print help + exit 2，不自動 scout（避免誤打觸發真 publish）
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
