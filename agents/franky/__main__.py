"""Franky CLI entry point.

Usage:
    python -m agents.franky              # legacy weekly report (backward-compat for current cron)
    python -m agents.franky health       # Slice 1: run one health-check tick + dispatch alerts
    python -m agents.franky alert --test # Slice 2: send a test alert through the router
    python -m agents.franky backup-verify  # Slice 2: verify R2 daily snapshot
    python -m agents.franky digest       # Slice 3: weekly digest (5-section Slack DM)
    python -m agents.franky anomaly      # Phase 5B-3: 15-min anomaly daemon tick

The default (no-subcommand) path preserves existing cron behavior until Slice 3 flips
the default to the new digest. See ADR-007 §11 for the module layout plan.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from shared.heartbeat import record_failure, record_success

# Windows cp1252 stdout 無法印中文 — 統一 UTF-8（feedback_windows_stdout_utf8）
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# Phase 5B-2 — heartbeat keys consumed by probe_cron_freshness via CRON_SCHEDULES.
# Stable across releases (changing breaks the probe's prior-state continuity).
_JOB_NAME_BACKUP_VERIFY = "franky-r2-backup-verify"
_JOB_NAME_DIGEST = "franky-weekly-report"
_JOB_NAME_NEWS = "franky-news-digest"
_JOB_NAME_ANOMALY = "nakama-anomaly-daemon"


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

    try:
        result = verify_once()
    except Exception as exc:
        record_failure(_JOB_NAME_BACKUP_VERIFY, f"{type(exc).__name__}: {exc}"[:200])
        raise

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
    # The cron itself succeeded (it ran and produced a verdict, possibly emitting an
    # alert about the *backed-up data* being stale). probe_cron_freshness watches the
    # cron's liveness; the backup-content alert path is probe_r2_backup_nakama's job.
    record_success(_JOB_NAME_BACKUP_VERIFY)
    # Exit 0 on ok/too-early-fail; exit 1 only if Critical alert emitted (cron noise signal)
    return 1 if result["alert"] is not None else 0


def _cmd_digest(_args: argparse.Namespace) -> int:
    from agents.franky.weekly_digest import send_digest

    try:
        result = send_digest()
    except Exception as exc:
        record_failure(_JOB_NAME_DIGEST, f"{type(exc).__name__}: {exc}"[:200])
        raise
    print(json.dumps(result, ensure_ascii=False, indent=2))
    record_success(_JOB_NAME_DIGEST)
    return 0


def _cmd_news(args: argparse.Namespace) -> int:
    """Slice A: AI ecosystem daily digest from official blogs."""
    from agents.franky.news_digest import run_news_digest

    dry_run = getattr(args, "dry_run", False)
    try:
        summary = run_news_digest(
            dry_run=dry_run,
            no_publish=getattr(args, "no_publish", False),
        )
    except Exception as exc:
        # dry-run is manual / ad-hoc — recording its failures would corrupt the
        # cron staleness signal. Only the production path emits heartbeats.
        if not dry_run:
            record_failure(_JOB_NAME_NEWS, f"{type(exc).__name__}: {exc}"[:200])
        raise
    print(summary)
    if not dry_run:
        record_success(_JOB_NAME_NEWS)
    return 0


def _cmd_anomaly(_args: argparse.Namespace) -> int:
    """Phase 5B-3 — one anomaly daemon tick (cost / latency / error rate / cron cluster).

    ``run_once`` itself records ``record_success`` on the happy path; the
    outer ``record_failure`` here only fires if the daemon crashed before
    completing (e.g. SQLite locked, disk full). Per-check exception
    isolation lives inside ``run_once``.
    """
    from agents.franky.anomaly_daemon import run_once

    try:
        anomalies = run_once()
    except Exception as exc:
        record_failure(_JOB_NAME_ANOMALY, f"{type(exc).__name__}: {exc}"[:200])
        raise
    summary = {
        "count": len(anomalies),
        "anomalies": [
            {
                "metric": a.metric,
                "target": a.target,
                "current": a.current,
                "baseline_mean": a.baseline_mean,
                "baseline_stddev": a.baseline_stddev,
                "sample_size": a.sample_size,
            }
            for a in anomalies
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
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
    sub.add_parser("anomaly", help="Phase 5B-3: anomaly daemon tick (cron every 15 min)")
    news = sub.add_parser("news", help="AI ecosystem daily digest (Slice A: official blogs)")
    news.add_argument(
        "--dry-run",
        action="store_true",
        help="Run pipeline without writing vault, sending Slack, or persisting state.",
    )
    news.add_argument(
        "--no-publish",
        action="store_true",
        help="Write vault digest but skip Slack DM (dev use).",
    )
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
        "news": _cmd_news,
        "anomaly": _cmd_anomaly,
    }
    handler = dispatch.get(args.command, _cmd_legacy_weekly)
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
