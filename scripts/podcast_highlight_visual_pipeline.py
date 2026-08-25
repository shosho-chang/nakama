"""Operate the hash-bound Podcast Highlight Director -> DP visual contract.

This CLI never connects to Resolve and never performs creative judgement.  It
only preflights current production inputs, initializes an immutable generation,
accepts strict worker proposals, and verifies the resulting lineage DAG.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.brook.script_video.highlight_visual_pipeline import (  # noqa: E402
    HighlightVisualContractError,
    accept_director_plan,
    accept_dp_fulfillment,
    accept_semantic_audit,
    init_visual_work_packet,
    preflight_visual_work_packet,
    verify_visual_pipeline,
    visual_pipeline_status,
)

logger = logging.getLogger("podcast_highlight_visual_pipeline")


def _add_episode_cut(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("episode", help="episode root containing highlights/")
    parser.add_argument("--cut-id", required=True, help="winner cut id, e.g. value-L01")


def _add_revision_request(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--revision-request",
        help=(
            "episode-local finished-review revision request; required once a "
            "CURRENT visual generation exists"
        ),
    )


def _add_acceptance_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--revision-id", required=True)
    parser.add_argument("--proposal", required=True, help="worker proposal JSON path")
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--execution-id", required=True)
    parser.add_argument("--session-id", required=True)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Podcast long-highlight visual contract: immutable "
            "Director -> DP -> Director semantic audit"
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)

    preflight = commands.add_parser(
        "preflight", help="read-only resolve the prospective revision and upstream hashes"
    )
    _add_episode_cut(preflight)
    _add_revision_request(preflight)

    init = commands.add_parser("init", help="initialize one immutable pending visual generation")
    _add_episode_cut(init)
    _add_revision_request(init)

    status = commands.add_parser("status", help="read-only current/pending state")
    _add_episode_cut(status)

    for command, help_text in (
        ("accept-director", "strict-parse and publish a Director proposal"),
        ("accept-dp", "strict-parse and publish a DP fulfillment proposal"),
        ("accept-audit", "strict-parse and publish the Director semantic audit"),
    ):
        accept = commands.add_parser(command, help=help_text)
        _add_episode_cut(accept)
        _add_acceptance_args(accept)

    verify = commands.add_parser(
        "verify", help="read-only verify CURRENT or an explicit immutable generation"
    )
    _add_episode_cut(verify)
    verify.add_argument("--revision-id")
    return parser.parse_args(argv)


def _worker_identity(args: argparse.Namespace, *, role: str) -> dict[str, str]:
    return {
        "worker_id": args.worker_id,
        "execution_id": args.execution_id,
        "role": role,
        "session_id": args.session_id,
    }


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _parse_args(argv)
    try:
        if args.command == "preflight":
            _print(
                preflight_visual_work_packet(
                    args.episode,
                    cut_id=args.cut_id,
                    revision_request=args.revision_request,
                )
            )
            return 0
        if args.command == "init":
            selected = init_visual_work_packet(
                args.episode,
                cut_id=args.cut_id,
                revision_request=args.revision_request,
            )
            _print(
                {
                    "status": "initialized",
                    "revision_id": selected.document["revision_id"],
                    "identity": selected.identity(),
                }
            )
            return 0
        if args.command == "status":
            payload = visual_pipeline_status(args.episode, cut_id=args.cut_id)
            _print(payload)
            return 0 if payload["status"] != "invalid" else 1
        if args.command == "accept-director":
            selected = accept_director_plan(
                args.episode,
                cut_id=args.cut_id,
                revision_id=args.revision_id,
                proposal=args.proposal,
                worker_identity=_worker_identity(args, role="director"),
            )
        elif args.command == "accept-dp":
            selected = accept_dp_fulfillment(
                args.episode,
                cut_id=args.cut_id,
                revision_id=args.revision_id,
                proposal=args.proposal,
                worker_identity=_worker_identity(args, role="dp"),
            )
        elif args.command == "accept-audit":
            selected = accept_semantic_audit(
                args.episode,
                cut_id=args.cut_id,
                revision_id=args.revision_id,
                proposal=args.proposal,
                worker_identity=_worker_identity(args, role="director"),
            )
        else:
            verified = verify_visual_pipeline(
                args.episode,
                cut_id=args.cut_id,
                revision_id=args.revision_id,
            )
            _print({"status": "verified", "lineage": verified.lineage()})
            return 0

        _print(
            {
                "status": "accepted",
                "revision_id": args.revision_id,
                "identity": selected.identity(),
            }
        )
        return 0
    except (HighlightVisualContractError, FileNotFoundError) as error:
        logger.error("%s", error)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
