"""Short-only Campaign Anchor due scanner and desktop dispatcher.

Dry-run is the default and is strictly read-only.  Live execution is explicit
and delegates the only supported automatic target (Instagram Reels) to the
existing atomic Release Target dispatcher.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.usopp.social_publish import SocialPublishAdapter, dispatch_release
from scripts.publish_dispatch import (
    _adapter_setup_error,
    build_short_adapters,
    write_json_output,
)
from shared import heartbeat
from shared.release_store import (
    TARGET_CLAIM_STALE_AFTER,
    get_release,
    get_release_campaign_anchor,
    list_releases,
)

JOB_NAME = "usopp-short-due-dispatcher"


def _aware_utc(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _clock(now: datetime | None) -> datetime:
    current = now or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("due scanner clock must be timezone-aware")
    return current.astimezone(UTC)


def _validated_scope(
    episode: str | None,
    cut: str | None,
) -> tuple[str | None, str | None]:
    if (episode is None) != (cut is None):
        raise ValueError("--episode and --cut must be provided together")
    if episode is None:
        return None, None
    scope_episode = episode.strip()
    scope_cut = cut.strip() if cut is not None else ""
    if not scope_episode or not scope_cut:
        raise ValueError("--episode and --cut must both be non-empty")
    return scope_episode, scope_cut


def scan_due(
    *,
    now: datetime | None = None,
    episode: str | None = None,
    cut: str | None = None,
) -> dict[str, Any]:
    """Return a portable, secret-free plan without claiming or writing state."""

    current = _clock(now)
    scope_episode, scope_cut = _validated_scope(episode, cut)
    if scope_episode and scope_cut:
        scoped_release = get_release(scope_episode, scope_cut)
        if scoped_release is None:
            raise ValueError(f"scoped Release does not exist: {scope_episode}/{scope_cut}")
        summaries = [scoped_release]
    else:
        summaries = list_releases()
    candidates: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    diagnostics: list[dict[str, str]] = []
    counts: Counter[str] = Counter()
    for summary in summaries:
        release_episode = str(summary.get("episode") or "")
        cut_id = str(summary.get("cut_id") or "")
        if summary.get("format") != "short":
            counts["excluded_non_short"] += 1
            continue
        release = get_release(release_episode, cut_id)
        if release is None:
            counts["excluded_disappeared"] += 1
            diagnostics.append(
                {"code": "release_disappeared", "release": f"{release_episode}/{cut_id}"}
            )
            continue
        if not release.get("targets"):
            counts["excluded_targets_missing"] += 1
            diagnostics.append(
                {
                    "code": "excluded_targets_missing",
                    "release": f"{release_episode}/{cut_id}",
                }
            )
            continue
        anchor = get_release_campaign_anchor(release_episode, cut_id)
        if anchor.state != "shared" or anchor.anchor_at is None:
            code = (
                "excluded_divergent_anchor"
                if anchor.state == "divergent"
                else "excluded_missing_anchor"
            )
            counts[code] += 1
            diagnostics.append({"code": code, "release": f"{release_episode}/{cut_id}"})
            continue
        anchor_at = anchor.anchor_at.astimezone(UTC)
        if anchor_at > current:
            counts["excluded_not_due"] += 1
            continue
        target = next(
            (
                item
                for item in release.get("targets", [])
                if item.get("platform") == "instagram_reels"
            ),
            None,
        )
        if target is None:
            counts["excluded_instagram_missing"] += 1
            diagnostics.append(
                {
                    "code": "excluded_instagram_missing",
                    "release": f"{release_episode}/{cut_id}",
                }
            )
            continue
        status = str(target.get("status") or "draft")
        plan_item = {
            "episode": release_episode,
            "cut_id": cut_id,
            "platform": "instagram_reels",
            "target_id": int(target["id"]),
            "anchor_at": anchor_at.isoformat(),
            "status": status,
        }
        if status == "approved":
            plan_item["claim_reason"] = "approved_due"
            candidates.append(plan_item)
            counts["due_candidates"] += 1
        elif status == "uploading":
            updated_at = _aware_utc(target.get("updated_at"))
            if updated_at is not None and updated_at <= current - TARGET_CLAIM_STALE_AFTER:
                plan_item["claim_reason"] = "stale_uploading_resume"
                candidates.append(plan_item)
                counts["due_candidates"] += 1
            else:
                counts["excluded_fresh_uploading"] += 1
        elif status == "failed":
            failed.append(plan_item)
            counts["due_failed_requires_retry"] += 1
        else:
            counts[f"excluded_instagram_{status}"] += 1
    return {
        "job": JOB_NAME,
        "scanned_at": current.isoformat(),
        "scope": (
            {"episode": scope_episode, "cut": scope_cut} if scope_episode and scope_cut else None
        ),
        "candidates": candidates,
        "failed": failed,
        "counts": dict(sorted(counts.items())),
        "diagnostics": diagnostics,
    }


def _live_adapters() -> tuple[Mapping[str, SocialPublishAdapter], Mapping[str, str]]:
    return build_short_adapters({"instagram_reels"})


def run_cycle(
    *,
    execute: bool = False,
    now: datetime | None = None,
    episode: str | None = None,
    cut: str | None = None,
    adapters: Mapping[str, SocialPublishAdapter] | None = None,
    record_success: Callable[[str], None] = heartbeat.record_success,
    record_failure: Callable[[str, str], None] = heartbeat.record_failure,
) -> tuple[int, dict[str, Any]]:
    """Scan once and optionally execute; failures stay isolated per Release."""

    try:
        current = _clock(now)
        scan_options: dict[str, Any] = {"now": current}
        if episode is not None or cut is not None:
            scan_options.update(episode=episode, cut=cut)
        plan = scan_due(**scan_options)
    except Exception as exc:
        payload = {
            "job": JOB_NAME,
            "dry_run": not execute,
            "scan_error": type(exc).__name__,
            "results": [],
        }
        if execute:
            try:
                record_failure(JOB_NAME, f"due scan failed: {type(exc).__name__}")
            except Exception:
                pass
        return 1, payload

    payload = {**plan, "dry_run": not execute, "results": []}
    if not execute:
        return 0, payload

    cycle_failures = len(plan["failed"])
    live_adapters = adapters
    adapter_setup_errors: Mapping[str, str] = {}
    if plan["candidates"] and live_adapters is None:
        try:
            live_adapters, adapter_setup_errors = _live_adapters()
        except Exception as exc:
            setup_error = _adapter_setup_error("instagram_reels", "Due Dispatcher startup", exc)
            payload["setup_error"] = setup_error
            adapter_setup_errors = {"instagram_reels": setup_error}
            live_adapters = {}
        if adapter_setup_errors:
            payload["setup_errors"] = dict(adapter_setup_errors)
    live_adapters = live_adapters or {}

    for candidate in plan["candidates"]:
        episode = candidate["episode"]
        cut_id = candidate["cut_id"]
        try:
            anchor = get_release_campaign_anchor(episode, cut_id)
            release = get_release(episode, cut_id)
            if release is None:
                raise ValueError("Release disappeared before dispatch")
            instagram = next(
                (
                    target
                    for target in release.get("targets", [])
                    if target.get("platform") == "instagram_reels"
                ),
                None,
            )
            expected_publish_at = instagram.get("publish_at") if instagram is not None else None
            release_anchor_values = [
                _aware_utc(target.get("publish_at")) for target in release.get("targets", [])
            ]
            anchor_still_due = (
                anchor.state == "shared"
                and anchor.anchor_at is not None
                and anchor.anchor_at.astimezone(UTC) <= current
                and instagram is not None
                and isinstance(expected_publish_at, str)
                and release_anchor_values
                and all(
                    value is not None and value == anchor.anchor_at.astimezone(UTC)
                    for value in release_anchor_values
                )
            )
            if not anchor_still_due:
                payload["results"].append(
                    {
                        "episode": episode,
                        "cut_id": cut_id,
                        "platform": "instagram_reels",
                        "status": str(
                            instagram.get("status") if instagram is not None else "missing"
                        ),
                        "called": False,
                        "skip_reason": "anchor_no_longer_due",
                    }
                )
                continue
            outcomes = dispatch_release(
                release,
                live_adapters,
                ["instagram_reels"],
                claim_now=current,
                expected_publish_at_by_platform={"instagram_reels": expected_publish_at},
                adapter_setup_errors=adapter_setup_errors,
            )
            outcome = (
                outcomes[0]
                if outcomes
                else {
                    "platform": "instagram_reels",
                    "status": "failed",
                    "called": False,
                    "error": "no dispatch outcome",
                }
            )
            result = {
                "episode": episode,
                "cut_id": cut_id,
                "platform": "instagram_reels",
                "status": outcome["status"],
                "called": bool(outcome.get("called")),
            }
            payload["results"].append(result)
            if outcome["status"] == "failed":
                cycle_failures += 1
        except Exception as exc:
            cycle_failures += 1
            payload["results"].append(
                {
                    "episode": episode,
                    "cut_id": cut_id,
                    "platform": "instagram_reels",
                    "status": "failed",
                    "called": False,
                    "error_type": type(exc).__name__,
                }
            )

    if cycle_failures:
        try:
            record_failure(JOB_NAME, f"{cycle_failures} due Instagram target(s) require attention")
        except Exception:
            return 1, {**payload, "heartbeat_error": "record_failure_failed"}
        return 1, payload
    try:
        record_success(JOB_NAME)
    except Exception:
        try:
            record_failure(JOB_NAME, "success heartbeat write failed")
        except Exception:
            pass
        return 1, {**payload, "heartbeat_error": "record_success_failed"}
    return 0, payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stage 6 Short Campaign Anchor due dispatcher")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="scan one cycle (default)")
    mode.add_argument("--watch", action="store_true", help="repeat cycles until interrupted")
    execution = parser.add_mutually_exclusive_group()
    execution.add_argument("--execute", action="store_true", help="allow live adapter calls")
    execution.add_argument("--dry-run", action="store_true", help="print read-only plans (default)")
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    parser.add_argument("--episode", help="exact supervised Release episode scope")
    parser.add_argument("--cut", help="exact supervised Release cut scope")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.episode, args.cut = _validated_scope(args.episode, args.cut)
    except ValueError as exc:
        build_parser().error(str(exc))
    if args.watch and args.poll_seconds <= 0:
        build_parser().error("--watch requires a positive --poll-seconds")
    if not args.watch:
        code, payload = run_cycle(
            execute=args.execute,
            episode=args.episode,
            cut=args.cut,
        )
        write_json_output(payload)
        return code
    last_code = 0
    try:
        while True:
            last_code, payload = run_cycle(
                execute=args.execute,
                episode=args.episode,
                cut=args.cut,
            )
            write_json_output(payload)
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        return last_code


if __name__ == "__main__":
    raise SystemExit(main())
