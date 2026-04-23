"""Franky CLI entry point.

Usage:
    python -m agents.franky              # legacy weekly report (backward-compat for current cron)
    python -m agents.franky health       # Slice 1: run one health-check tick
    python -m agents.franky alert        # Slice 2: alert router (not yet implemented)
    python -m agents.franky backup-verify  # Slice 2: R2 backup verify (not yet implemented)
    python -m agents.franky digest       # Slice 3: weekly digest (not yet implemented)

The default (no-subcommand) path preserves existing cron behavior until Slice 3 flips
the default to the new digest. See ADR-007 §11 for the module layout plan.
"""

from __future__ import annotations

import argparse
import json
import sys


def _cmd_health(_args: argparse.Namespace) -> int:
    from agents.franky.health_check import run_once

    result = run_once()
    summary = {
        "operation_id": result["operation_id"],
        "duration_ms": result["duration_ms"],
        "probes": [
            {
                "target": p.target,
                "status": p.status,
                "latency_ms": p.latency_ms,
                "error": p.error,
            }
            for p in result["probes"]
        ],
        "alerts": [
            {
                "rule_id": a.rule_id,
                "severity": a.severity,
                "dedup_key": a.dedup_key,
            }
            for a in result["alerts"]
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _cmd_alert(_args: argparse.Namespace) -> int:
    raise NotImplementedError(
        "agents.franky.alert_router lands in Slice 2 (feature/franky-slice-2-alert-backup)"
    )


def _cmd_backup_verify(_args: argparse.Namespace) -> int:
    raise NotImplementedError(
        "agents.franky.r2_backup_verify lands in Slice 2 (feature/franky-slice-2-alert-backup)"
    )


def _cmd_digest(_args: argparse.Namespace) -> int:
    raise NotImplementedError(
        "agents.franky.weekly_digest lands in Slice 3 (feature/franky-slice-3-digest-dashboard)"
    )


def _cmd_legacy_weekly(_args: argparse.Namespace) -> int:
    """Backward-compat: the current VPS cron still runs `python -m agents.franky` without args."""
    from agents.franky.agent import FrankyAgent

    FrankyAgent().execute()
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agents.franky", description="Franky maintenance agent")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("health", help="Run one health-check tick (Slice 1)")
    sub.add_parser("alert", help="Alert router (Slice 2, not yet implemented)")
    sub.add_parser("backup-verify", help="R2 backup verify (Slice 2, not yet implemented)")
    sub.add_parser("digest", help="Weekly digest (Slice 3, not yet implemented)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    dispatch = {
        "health": _cmd_health,
        "alert": _cmd_alert,
        "backup-verify": _cmd_backup_verify,
        "digest": _cmd_digest,
    }
    handler = dispatch.get(args.command, _cmd_legacy_weekly)
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
