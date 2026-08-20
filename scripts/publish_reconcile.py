"""Stage 6 Outcome Reconciler for already Native-Armed Release Targets.

Dry-run is the default and performs only local reads. Execute mode performs one
platform GET per eligible target, then confirms explicit outcomes through an
``uploaded + video_id + updated_at`` compare-and-set. It never publishes,
uploads, recreates, retries, or changes Campaign Anchors.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.publish_dispatch import build_meta_client, write_json_output
from scripts.publish_upload import load_youtube_observer, observe_youtube_video
from shared import heartbeat
from shared.release_store import (
    confirm_target_outcome,
    get_release,
    get_release_campaign_anchor,
    list_releases,
)

JOB_NAME = "usopp-release-outcome-reconciler"
ELIGIBLE_PLATFORMS = frozenset({"youtube", "facebook_reels"})
_EVIDENCE_CATEGORIES = frozenset(
    {
        "public",
        "private",
        "processing",
        "scheduled",
        "processing_failed",
        "publishing_failed",
        "unsafe_permalink",
        "unknown",
    }
)


class OutcomeObservation(Protocol):
    outcome: str
    evidence_category: str
    certain: bool
    permalink: str | None
    error: str | None


Observer = Callable[[str], OutcomeObservation]


@dataclass(frozen=True, slots=True)
class _Candidate:
    episode: str
    cut_id: str
    platform: str
    target_id: int
    anchor_at: str
    status: str
    video_id: str
    updated_at: str

    def report(self, evidence_category: str = "observation_required") -> dict[str, Any]:
        return {
            "episode": self.episode,
            "cut_id": self.cut_id,
            "platform": self.platform,
            "target_id": self.target_id,
            "anchor_at": self.anchor_at,
            "status": self.status,
            "evidence_category": evidence_category,
        }


def _clock(now: datetime | None) -> datetime:
    current = now or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Outcome Reconciler clock must be timezone-aware")
    return current.astimezone(UTC)


def _validated_scope(
    episode: str | None,
    cut: str | None,
) -> tuple[str | None, str | None]:
    if (episode is None) != (cut is None):
        raise ValueError("--episode and --cut must be provided together")
    if episode is None:
        return None, None
    scoped_episode = episode.strip()
    scoped_cut = cut.strip() if cut is not None else ""
    if not scoped_episode or not scoped_cut:
        raise ValueError("--episode and --cut must both be non-empty")
    return scoped_episode, scoped_cut


def _safe_evidence(value: object) -> str:
    candidate = str(value or "").strip()
    return candidate if candidate in _EVIDENCE_CATEGORIES else "unknown"


def _diagnostic(
    code: str,
    *,
    episode: str,
    cut_id: str,
    platform: str | None = None,
    target_id: int | None = None,
    anchor_at: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "episode": episode,
        "cut_id": cut_id,
        "platform": platform,
        "target_id": target_id,
        "anchor_at": anchor_at,
        "status": status,
        "evidence_category": code,
    }


def _scan_outcomes(
    *,
    now: datetime | None = None,
    episode: str | None = None,
    cut: str | None = None,
) -> tuple[dict[str, Any], list[_Candidate]]:
    current = _clock(now)
    scoped_episode, scoped_cut = _validated_scope(episode, cut)
    all_releases = []
    for summary in list_releases():
        release = get_release(str(summary["episode"]), str(summary["cut_id"]))
        if release is not None:
            all_releases.append(release)
    identity_inventory = Counter(
        (str(target.get("platform") or ""), str(target.get("video_id") or "").strip())
        for release in all_releases
        for target in release.get("targets", [])
        if target.get("platform") in ELIGIBLE_PLATFORMS
        and str(target.get("video_id") or "").strip()
    )
    if scoped_episode and scoped_cut:
        scoped = get_release(scoped_episode, scoped_cut)
        if scoped is None:
            raise ValueError(f"scoped Release does not exist: {scoped_episode}/{scoped_cut}")
        releases = [scoped]
    else:
        releases = all_releases

    candidates: list[_Candidate] = []
    diagnostics: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for release in releases:
        release_episode = str(release.get("episode") or "")
        cut_id = str(release.get("cut_id") or "")
        targets = list(release.get("targets") or [])
        if not targets:
            diagnostics.append(
                _diagnostic("missing_release_targets", episode=release_episode, cut_id=cut_id)
            )
            counts["missing_release_targets"] += 1
            continue
        anchor = get_release_campaign_anchor(release_episode, cut_id)
        if anchor.state != "shared" or anchor.anchor_at is None:
            code = (
                "divergent_campaign_anchor"
                if anchor.state == "divergent"
                else "missing_campaign_anchor"
            )
            if any(
                target.get("platform") in ELIGIBLE_PLATFORMS and target.get("status") == "uploaded"
                for target in targets
            ):
                diagnostics.append(_diagnostic(code, episode=release_episode, cut_id=cut_id))
                counts[code] += 1
            continue
        anchor_at = anchor.anchor_at.astimezone(UTC)
        if anchor_at > current:
            counts["future_anchor"] += 1
            continue
        for target in targets:
            platform = str(target.get("platform") or "")
            status = str(target.get("status") or "draft")
            if platform not in ELIGIBLE_PLATFORMS:
                counts["ineligible_platform"] += 1
                continue
            if status != "uploaded":
                counts[f"status_{status}"] += 1
                continue
            target_id = int(target["id"])
            video_id = str(target.get("video_id") or "").strip()
            updated_at = str(target.get("updated_at") or "").strip()
            if not video_id or not updated_at:
                diagnostics.append(
                    _diagnostic(
                        "missing_video_identity",
                        episode=release_episode,
                        cut_id=cut_id,
                        platform=platform,
                        target_id=target_id,
                        anchor_at=anchor_at.isoformat(),
                        status=status,
                    )
                )
                counts["missing_video_identity"] += 1
                continue
            candidates.append(
                _Candidate(
                    episode=release_episode,
                    cut_id=cut_id,
                    platform=platform,
                    target_id=target_id,
                    anchor_at=anchor_at.isoformat(),
                    status=status,
                    video_id=video_id,
                    updated_at=updated_at,
                )
            )

    unique_candidates: list[_Candidate] = []
    for candidate in candidates:
        if identity_inventory[(candidate.platform, candidate.video_id)] > 1:
            diagnostics.append(
                _diagnostic(
                    "duplicate_platform_identity",
                    episode=candidate.episode,
                    cut_id=candidate.cut_id,
                    platform=candidate.platform,
                    target_id=candidate.target_id,
                    anchor_at=candidate.anchor_at,
                    status=candidate.status,
                )
            )
            counts["duplicate_platform_identity"] += 1
        else:
            unique_candidates.append(candidate)
    counts["candidates"] = len(unique_candidates)
    report = {
        "job": JOB_NAME,
        "scanned_at": current.isoformat(),
        "scope": (
            {"episode": scoped_episode, "cut": scoped_cut}
            if scoped_episode and scoped_cut
            else None
        ),
        "candidates": [candidate.report() for candidate in unique_candidates],
        "counts": dict(sorted(counts.items())),
        "diagnostics": diagnostics,
    }
    return report, unique_candidates


def scan_outcomes(
    *,
    now: datetime | None = None,
    episode: str | None = None,
    cut: str | None = None,
) -> dict[str, Any]:
    """Return a secret-free local observation plan with no external calls."""

    report, _ = _scan_outcomes(now=now, episode=episode, cut=cut)
    return report


def _live_observers(
    platforms: set[str],
) -> tuple[dict[str, Observer], dict[str, str]]:
    observers: dict[str, Observer] = {}
    errors: dict[str, str] = {}
    if "youtube" in platforms:
        try:
            youtube = load_youtube_observer()
            observers["youtube"] = lambda video_id: observe_youtube_video(youtube, video_id)
        except (Exception, SystemExit):
            errors["youtube"] = "youtube_observer_setup_failed"
    if "facebook_reels" in platforms:
        try:
            meta = build_meta_client()
            observers["facebook_reels"] = meta.observe_facebook_reel
        except (Exception, SystemExit):
            errors["facebook_reels"] = "facebook_observer_setup_failed"
    return observers, errors


def run_cycle(
    *,
    execute: bool = False,
    now: datetime | None = None,
    episode: str | None = None,
    cut: str | None = None,
    observers: Mapping[str, Observer] | None = None,
    record_success: Callable[[str], None] = heartbeat.record_success,
    record_failure: Callable[[str, str], None] = heartbeat.record_failure,
) -> tuple[int, dict[str, Any]]:
    """Scan once and optionally observe; uncertainty is isolated per target."""

    global_heartbeat = episode is None and cut is None
    try:
        report, candidates = _scan_outcomes(
            now=now,
            episode=episode,
            cut=cut,
        )
    except Exception as exc:
        payload = {
            "job": JOB_NAME,
            "dry_run": not execute,
            "scan_error": type(exc).__name__,
            "results": [],
        }
        if execute and global_heartbeat:
            try:
                record_failure(JOB_NAME, "Outcome Reconciler scan failed")
            except Exception:
                pass
        return 1, payload

    payload = {**report, "dry_run": not execute, "results": []}
    if not execute:
        return 0, payload

    failures = len(report["diagnostics"])
    live_observers = dict(observers) if observers is not None else None
    setup_errors: Mapping[str, str] = {}
    if candidates and live_observers is None:
        requested_platforms = {candidate.platform for candidate in candidates}
        try:
            live_observers, setup_errors = _live_observers(requested_platforms)
        except (Exception, SystemExit):
            live_observers = {}
            setup_errors = {platform: "observer_setup_failed" for platform in requested_platforms}
    live_observers = live_observers or {}

    for candidate in candidates:
        observer = live_observers.get(candidate.platform)
        if observer is None:
            failures += 1
            evidence = setup_errors.get(candidate.platform, "observer_unavailable")
            payload["results"].append(candidate.report(evidence))
            continue
        try:
            observation = observer(candidate.video_id)
        except (Exception, SystemExit):
            failures += 1
            payload["results"].append(candidate.report("observation_error"))
            continue
        evidence = _safe_evidence(observation.evidence_category)
        if not observation.certain:
            failures += 1
            payload["results"].append(candidate.report(evidence))
            continue
        if observation.outcome == "pending":
            payload["results"].append(candidate.report(evidence))
            continue
        if observation.outcome not in {"published", "failed"}:
            failures += 1
            payload["results"].append(candidate.report("unknown"))
            continue
        platform_label = "YouTube" if candidate.platform == "youtube" else "Facebook"
        durable_error = (
            f"{platform_label} outcome confirmed: {evidence}"
            if observation.outcome == "failed"
            else None
        )
        try:
            won = confirm_target_outcome(
                candidate.target_id,
                expected_video_id=candidate.video_id,
                expected_updated_at=candidate.updated_at,
                status=observation.outcome,
                url=observation.permalink,
                error=durable_error,
            )
        except Exception:
            failures += 1
            payload["results"].append(candidate.report("cas_error"))
            continue
        if not won:
            stale_result = candidate.report("stale_snapshot")
            try:
                current_release = get_release(candidate.episode, candidate.cut_id)
            except Exception:
                failures += 1
                payload["results"].append(candidate.report("cas_error"))
                continue
            if current_release is not None:
                current_target = next(
                    (
                        target
                        for target in current_release.get("targets", [])
                        if target.get("id") == candidate.target_id
                    ),
                    None,
                )
                if current_target is not None:
                    stale_result["status"] = str(current_target.get("status") or "unknown")
            payload["results"].append(stale_result)
            continue
        result = candidate.report(evidence)
        result["status"] = observation.outcome
        payload["results"].append(result)

    if failures:
        if not global_heartbeat:
            payload["heartbeat_scope"] = "suppressed_exact_scope"
            return 1, payload
        try:
            record_failure(JOB_NAME, f"{failures} outcome target(s) require attention")
        except Exception:
            return 1, {**payload, "heartbeat_error": "record_failure_failed"}
        return 1, payload
    if not global_heartbeat:
        payload["heartbeat_scope"] = "suppressed_exact_scope"
        return 0, payload
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
    parser = argparse.ArgumentParser(description="Stage 6 Release Outcome Reconciler")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="scan one cycle (default)")
    mode.add_argument("--watch", action="store_true", help="repeat supervised cycles")
    execution = parser.add_mutually_exclusive_group()
    execution.add_argument("--execute", action="store_true", help="allow platform GET + local CAS")
    execution.add_argument("--dry-run", action="store_true", help="local read-only plan (default)")
    parser.add_argument("--poll-seconds", type=float, default=300.0)
    parser.add_argument("--episode", help="exact supervised Release episode scope")
    parser.add_argument("--cut", help="exact supervised Release cut scope")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.episode, args.cut = _validated_scope(args.episode, args.cut)
    except ValueError as exc:
        parser.error(str(exc))
    if args.watch and args.poll_seconds <= 0:
        parser.error("--watch requires a positive --poll-seconds")
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
