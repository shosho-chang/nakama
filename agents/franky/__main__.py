"""Franky CLI entry point.

Usage:
    python -m agents.franky              # legacy weekly report (backward-compat for current cron)
    python -m agents.franky health       # Slice 1: run one health-check tick + dispatch alerts
    python -m agents.franky alert --test # Slice 2: send a test alert through the router
    python -m agents.franky backup-verify  # Slice 2: verify R2 daily snapshot
    python -m agents.franky digest       # Slice 3: weekly digest (5-section Slack DM)

The default (no-subcommand) path preserves existing cron behavior until Slice 3 flips
the default to the new digest. See ADR-007 §11 for the module layout plan.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone


def _cmd_health(_args: argparse.Namespace) -> int:
    from agents.franky.alert_router import make_default_sink
    from agents.franky.health_check import run_once

    sink = make_default_sink()
    result = run_once(alert_sink=sink)
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


def _cmd_alert(args: argparse.Namespace) -> int:
    """Test path — send a synthetic AlertV1 through the router to verify Slack wiring."""
    if not getattr(args, "test", False):
        print("Run `python -m agents.franky alert --test` to send a test alert.", file=sys.stderr)
        return 2

    import uuid

    from agents.franky.alert_router import dispatch
    from agents.franky.slack_bot import FrankySlackBot
    from shared.schemas.franky import AlertV1

    alert = AlertV1(
        rule_id="franky_alert_self_test",
        severity="info",
        title="Franky self-test",
        message="If you can read this in Slack, alert_router + slack_bot are wired correctly.",
        fired_at=datetime.now(timezone.utc),
        dedup_key=f"franky_alert_self_test_{uuid.uuid4().hex[:8]}",
        dedup_window_seconds=60,
        operation_id=f"op_{uuid.uuid4().hex[:8]}",
        context={"mode": "self_test"},
    )
    result = dispatch(alert, slack_bot=FrankySlackBot.from_env())
    print(json.dumps({"alert_rule": alert.rule_id, **result}, ensure_ascii=False, indent=2))
    return 0


def _cmd_backup_verify(_args: argparse.Namespace) -> int:
    from agents.franky.alert_router import make_default_sink
    from agents.franky.r2_backup_verify import verify_once

    result = verify_once()
    summary = {
        "operation_id": result["operation_id"],
        "status": result["status"],
        "detail": result["detail"],
        "alert_emitted": result["alert"] is not None,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    # Dispatch any critical alert through the normal router (dedup + Slack)
    if result["alert"] is not None:
        sink = make_default_sink()
        sink(result["alert"])
    # Exit 0 on ok/too-early-fail; exit 1 only if Critical alert emitted (cron noise signal)
    return 1 if result["alert"] is not None else 0


def _cmd_digest(_args: argparse.Namespace) -> int:
    from agents.franky.weekly_digest import send_digest

    result = send_digest()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_legacy_weekly(_args: argparse.Namespace) -> int:
    """Backward-compat: the current VPS cron still runs `python -m agents.franky` without args."""
    from agents.franky.agent import FrankyAgent

    FrankyAgent().execute()
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agents.franky", description="Franky maintenance agent")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("health", help="Run one health-check tick (Slice 1+2)")
    alert = sub.add_parser("alert", help="Alert router self-test (Slice 2)")
    alert.add_argument(
        "--test",
        action="store_true",
        help="Send a synthetic info alert through the router (verifies Slack wiring).",
    )
    sub.add_parser("backup-verify", help="Verify R2 daily backup (Slice 2)")
    sub.add_parser("digest", help="Send weekly digest to Slack DM (Slice 3)")
    return parser


def main(argv: list[str] | None = None) -> int:
    # Must run before any subcommand reads env (e.g. SlackBot.from_env in make_default_sink).
    # Other agents trigger this indirectly via get_db_path(); Franky can send an alert
    # without touching the DB first, so we load explicitly here.
    from shared.config import load_config

    load_config()

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
