"""Franky CLI entry point.

Usage:
    python -m agents.franky              # legacy weekly report (backward-compat for current cron)
    python -m agents.franky health       # Slice 1: run one health-check tick + dispatch alerts
    python -m agents.franky alert --test # Slice 2: send a test alert through the router
    python -m agents.franky backup-verify  # Slice 2: verify R2 daily snapshot
    python -m agents.franky digest       # Slice 3: weekly digest (5-section Slack DM)
    python -m agents.franky anomaly      # Phase 5B-3: 15-min anomaly daemon tick
    python -m agents.franky gsc-daily    # ADR-008 Phase 2a-min: daily 7-day GSC pull → state.db
    python -m agents.franky synthesis    # ADR-023 §7 S3: weekly synthesis → proposal inbox
    python -m agents.franky retrospective  # ADR-023 §7 S4: monthly retrospective

The default (no-subcommand) path preserves existing cron behavior until Slice 3 flips
the default to the new digest. See ADR-007 §11 for the module layout plan.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

# Windows cp1252 stdout 無法印中文 — 統一 UTF-8（feedback_windows_stdout_utf8）.
# Must run before imports that build module-level loggers (StreamHandler
# captures sys.stdout at attach time).
from shared.log import force_utf8_console

force_utf8_console()

from shared.heartbeat import record_failure, record_success  # noqa: E402

# Phase 5B-2 — heartbeat keys consumed by probe_cron_freshness via CRON_SCHEDULES.
# Stable across releases (changing breaks the probe's prior-state continuity).
_JOB_NAME_BACKUP_VERIFY = "franky-r2-backup-verify"
_JOB_NAME_DIGEST = "franky-weekly-report"
_JOB_NAME_NEWS = "franky-news-digest"
_JOB_NAME_ANOMALY = "nakama-anomaly-daemon"
_JOB_NAME_GSC_DAILY = "franky-gsc-daily"
_JOB_NAME_SYNTHESIS = "franky-news-synthesis"
_JOB_NAME_RETROSPECTIVE = "franky-news-retrospective"


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
    from agents.franky.r2_backup_verify import verify_all_prefixes

    try:
        results = verify_all_prefixes()
    except Exception as exc:
        record_failure(_JOB_NAME_BACKUP_VERIFY, f"{type(exc).__name__}: {exc}"[:200])
        raise

    summary = [
        {
            "prefix": r["prefix"],
            "operation_id": r["operation_id"],
            "status": r["status"],
            "detail": r["detail"],
            "alert_emitted": r["alert"] is not None,
        }
        for r in results
    ]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    # Dispatch any critical alerts through the normal router (per-prefix dedup + Slack)
    sink = make_default_sink()
    for r in results:
        if r["alert"] is not None:
            sink(r["alert"])
    # The cron itself succeeded (it ran and produced a verdict, possibly emitting an
    # alert about the *backed-up data* being stale). probe_cron_freshness watches the
    # cron's liveness; the backup-content alert path is probe_r2_backup_nakama's job.
    record_success(_JOB_NAME_BACKUP_VERIFY)
    # Exit 0 on ok/too-early-fail; exit 1 only if any prefix emitted Critical alert
    return 1 if any(r["alert"] is not None for r in results) else 0


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


def _cmd_gsc_daily(args: argparse.Namespace) -> int:
    """ADR-008 Phase 2a-min — daily 7-day GSC pull → state.db gsc_rows.

    Heartbeats: success on every non-fail verdict (skipped is fine — env not
    yet provisioned is a config gap, not a cron-stuck signal). Failure
    heartbeat fires only when the cron itself crashed or ``status='fail'``
    (all keywords failed) so probe_cron_freshness doesn't false-positive
    during early bring-up.
    """
    from agents.franky.jobs.gsc_daily import run_once

    dry_run = getattr(args, "dry_run", False)
    try:
        result = run_once(dry_run=dry_run)
    except Exception as exc:
        if not dry_run:
            record_failure(_JOB_NAME_GSC_DAILY, f"{type(exc).__name__}: {exc}"[:200])
        raise

    print(json.dumps(result.to_summary_dict(), ensure_ascii=False, indent=2))

    if dry_run:
        return 0
    if result.status == "fail":
        record_failure(_JOB_NAME_GSC_DAILY, result.detail[:200])
        return 1
    record_success(_JOB_NAME_GSC_DAILY)
    return 0


def _cmd_synthesis(args: argparse.Namespace) -> int:
    """ADR-023 §7 S3 — weekly synthesis → two-stage proposal inbox."""
    from agents.franky.news_synthesis import _re_scan_and_promote_page, run_synthesis

    if getattr(args, "re_scan_promotions", False):
        target = getattr(args, "page", None)
        if not target:
            print(
                "--re-scan-promotions requires --page <vault-page-path>",
                file=__import__("sys").stderr,
            )
            return 2
        _re_scan_and_promote_page(target)
        return 0

    dry_run = getattr(args, "dry_run", False)
    try:
        summary = run_synthesis(
            dry_run=dry_run,
            no_publish=getattr(args, "no_publish", False),
        )
    except Exception as exc:
        if not dry_run:
            record_failure(_JOB_NAME_SYNTHESIS, f"{type(exc).__name__}: {exc}"[:200])
        raise
    print(summary)
    if not dry_run:
        record_success(_JOB_NAME_SYNTHESIS)
    return 0


def _cmd_retrospective(args: argparse.Namespace) -> int:
    """ADR-023 §7 S4 — monthly retrospective → metric_type 三類處理 + vault + Slack."""
    from agents.franky.news_retrospective import run_retrospective

    dry_run = getattr(args, "dry_run", False)
    try:
        summary = run_retrospective(
            dry_run=dry_run,
            no_publish=getattr(args, "no_publish", False),
        )
    except Exception as exc:
        if not dry_run:
            record_failure(_JOB_NAME_RETROSPECTIVE, f"{type(exc).__name__}: {exc}"[:200])
        raise
    print(summary)
    if not dry_run:
        record_success(_JOB_NAME_RETROSPECTIVE)
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
    gsc = sub.add_parser(
        "gsc-daily", help="ADR-008 Phase 2a-min: daily 7-day GSC pull → state.db gsc_rows"
    )
    gsc.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse keywords + compute window only; no GSC API call, no DB write.",
    )
    synthesis = sub.add_parser(
        "synthesis",
        help="ADR-023 §7 S3: weekly synthesis → two-stage proposal inbox (週日 22:00)",
    )
    synthesis.add_argument(
        "--dry-run",
        action="store_true",
        help="Run pipeline without writing vault, DB insert, or Slack DM.",
    )
    synthesis.add_argument(
        "--no-publish",
        action="store_true",
        help="Write vault + DB but skip Slack DM.",
    )
    synthesis.add_argument(
        "--re-scan-promotions",
        action="store_true",
        help="Scan a Weekly vault page for promote=true candidates and open GH issues.",
    )
    synthesis.add_argument(
        "--page",
        metavar="PATH",
        help="Path to vault page for --re-scan-promotions.",
    )
    retro = sub.add_parser(
        "retrospective",
        help="ADR-023 §7 S4: monthly retrospective — metric_type 三類處理 (月底最後週日 22:00)",
    )
    retro.add_argument(
        "--dry-run",
        action="store_true",
        help="Run pipeline without writing vault, DB updates, or Slack DM.",
    )
    retro.add_argument(
        "--no-publish",
        action="store_true",
        help="Write vault but skip Slack DM.",
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
        "gsc-daily": _cmd_gsc_daily,
        "synthesis": _cmd_synthesis,
        "retrospective": _cmd_retrospective,
    }
    handler = dispatch.get(args.command, _cmd_legacy_weekly)
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
