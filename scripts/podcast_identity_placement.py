"""Accept and verify quorum-backed guest identity-card placement."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.brook.script_video.identity_placement import (  # noqa: E402
    DEFAULT_MAX_INTRO_SEC,
    IdentityPlacementError,
    accept_identity_placement,
    emit_guest_namecard_recipe,
    identity_placement_status,
    verify_guest_namecard_recipe,
    verify_identity_placement,
)

logger = logging.getLogger("podcast_identity_placement")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Podcast guest identity placement: two independent SRT audits "
            "must agree before a guest namecard can be validated"
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)

    status = commands.add_parser("status", help="verify the cut-local receipt if present")
    status.add_argument("episode")
    status.add_argument("--cut-id", required=True)

    accept = commands.add_parser("accept", help="seal two exact-agreement worker audits")
    accept.add_argument("episode")
    accept.add_argument("--cut-id", required=True)
    accept.add_argument("--cut-srt", required=True)
    accept.add_argument("--audit-a", required=True)
    accept.add_argument("--audit-b", required=True)
    accept.add_argument("--max-intro-sec", type=float, default=DEFAULT_MAX_INTRO_SEC)

    verify = commands.add_parser(
        "verify", help="verify receipt lineage and the canonical guest-namecard event"
    )
    verify.add_argument("episode")
    verify.add_argument("--cut-id", required=True)
    verify.add_argument("--guest-namecard-start", type=float)
    verify.add_argument("--guest-namecard-end", type=float)

    emit = commands.add_parser(
        "emit-event", help="write the accepted cue into the existing B-roll renderer recipe"
    )
    emit.add_argument("episode")
    emit.add_argument("--cut-id", required=True)
    emit.add_argument("--name", required=True)
    emit.add_argument("--title", required=True)
    emit.add_argument("--duration-sec", type=float, default=5.2)
    emit.add_argument("--style", choices=("paper", "ink", "orange"), default="paper")
    return parser.parse_args(argv)


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _parse_args(argv)
    try:
        if args.command == "status":
            payload = identity_placement_status(args.episode, cut_id=args.cut_id)
            _print(payload)
            return 0 if payload["status"] == "ready" else 1
        if args.command == "accept":
            selected = accept_identity_placement(
                args.episode,
                cut_id=args.cut_id,
                cut_srt=args.cut_srt,
                audit_a=args.audit_a,
                audit_b=args.audit_b,
                max_intro_sec=args.max_intro_sec,
            )
            _print(
                {
                    "status": "accepted",
                    "receipt": str(selected.receipt_path),
                    "content_hash": selected.receipt["content_hash"],
                    "accepted_guest_cue": selected.receipt["accepted_guest_cue"],
                }
            )
            return 0
        if args.command == "emit-event":
            event = emit_guest_namecard_recipe(
                args.episode,
                cut_id=args.cut_id,
                name=args.name,
                title=args.title,
                duration_sec=args.duration_sec,
                style=args.style,
            )
            _print({"status": "emitted", "event": event})
            return 0
        if args.guest_namecard_start is None and args.guest_namecard_end is None:
            selected = verify_guest_namecard_recipe(args.episode, cut_id=args.cut_id)
        else:
            selected = verify_identity_placement(
                args.episode,
                cut_id=args.cut_id,
                guest_namecard_start=args.guest_namecard_start,
                guest_namecard_end=args.guest_namecard_end,
            )
        _print(
            {
                "status": "verified",
                "receipt": str(selected.receipt_path),
                "content_hash": selected.receipt["content_hash"],
            }
        )
        return 0
    except (IdentityPlacementError, FileNotFoundError) as error:
        logger.error("%s", error)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
