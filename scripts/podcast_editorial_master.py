"""Inspect, seal, and verify a human-approved Podcast Editorial Master."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.brook.script_video.editorial_master import (  # noqa: E402
    EditorialMasterContractError,
    EditorialMasterRequest,
    editorial_master_status,
    inspect_timeline,
    seal_editorial_master,
    verify_editorial_master,
)
from agents.brook.script_video.subtitle_handoff import (  # noqa: E402
    Stage5SubtitleContractError,
    Stage5SubtitleRequest,
)

logger = logging.getLogger("podcast_editorial_master")


def _request(args: argparse.Namespace) -> EditorialMasterRequest:
    return EditorialMasterRequest(
        episode_root=Path(args.episode),
        project_name=getattr(args, "project", None),
        timeline_name=getattr(args, "timeline", None),
        expected_timeline_uid=getattr(args, "expected_timeline_uid", None),
        expected_episode_id=getattr(args, "expected_episode_id", None),
        expected_content_hash=getattr(args, "expected_content_hash", None),
    )


def _add_identity_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--expected-episode-id")
    parser.add_argument("--expected-content-hash")
    parser.add_argument("--expected-timeline-uid")


def _add_resolve_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", help="Resolve project name; defaults to episode folder name")
    parser.add_argument("--timeline", help="approved Timeline name; defaults to project name")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Podcast Editorial Master：approved Resolve Timeline → immutable fan-out baseline"
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="read-only inspect the Resolve Timeline")
    inspect_parser.add_argument("episode")
    _add_resolve_args(inspect_parser)
    _add_identity_args(inspect_parser)

    status_parser = subparsers.add_parser("status", help="read-only local contract status")
    status_parser.add_argument("episode")

    verify_parser = subparsers.add_parser("verify", help="verify receipt and every artifact hash")
    verify_parser.add_argument("episode")
    _add_identity_args(verify_parser)
    _add_resolve_args(verify_parser)
    verify_parser.add_argument(
        "--live",
        action="store_true",
        help="also compare against the currently open Resolve Timeline",
    )

    seal_parser = subparsers.add_parser(
        "seal", help="render and seal an explicitly approved Timeline"
    )
    seal_parser.add_argument("episode")
    _add_resolve_args(seal_parser)
    _add_identity_args(seal_parser)
    seal_parser.add_argument(
        "--human-approved",
        action="store_true",
        required=True,
        help="explicitly assert the human approved this exact Timeline",
    )
    seal_parser.add_argument("--approved-by", required=True, help="human approval identity")
    seal_parser.add_argument("--approved-at", help="ISO-8601 approval time; defaults to now")
    seal_parser.add_argument(
        "--subtitle-release-handoff",
        help=(
            "episode-local official Memo Dual-Audit STAGE5-HANDOFF.json; "
            "omitted opens the canonical default"
        ),
    )
    return parser.parse_args(argv)


def _connect_resolve():
    from scripts.build_resolve_project import connect_resolve

    return connect_resolve()


def _print(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    args = _parse_args(argv)
    try:
        if args.command == "status":
            payload = editorial_master_status(args.episode)
            _print(payload)
            return 0 if payload["status"] == "ready" else 1

        request = _request(args)
        if args.command == "inspect":
            inspected = inspect_timeline(request, _connect_resolve())
            _print(
                {
                    "status": "inspected",
                    "snapshot": inspected.snapshot,
                    "master_srt_sha256": hashlib.sha256(
                        inspected.srt_text.encode("utf-8")
                    ).hexdigest(),
                    "timing_qc": inspected.timing_qc,
                }
            )
            return 0

        if args.command == "verify":
            live_snapshot = None
            if args.live:
                live_snapshot = inspect_timeline(request, _connect_resolve()).snapshot
            selected = verify_editorial_master(
                args.episode,
                expected_episode_id=args.expected_episode_id,
                expected_content_hash=args.expected_content_hash,
                expected_timeline_uid=args.expected_timeline_uid,
                live_snapshot=live_snapshot,
            )
            _print({"status": "verified", "identity": selected.identity()})
            return 0

        # The CLI never accepts operator-authored lineage JSON.  It re-opens the
        # official immutable Stage 5 handoff immediately before rendering.
        stage5 = Stage5SubtitleRequest(
            subtitle_release_handoff=args.subtitle_release_handoff,
        ).open(args.episode)
        selected = seal_editorial_master(
            request,
            _connect_resolve(),
            stage5_identity=stage5.identity(),
            human_approved=args.human_approved,
            approved_by=args.approved_by,
            approved_at=args.approved_at,
        )
        _print({"status": "sealed", "identity": selected.identity()})
        return 0
    except (
        EditorialMasterContractError,
        Stage5SubtitleContractError,
        FileNotFoundError,
    ) as error:
        logger.error("%s", error)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
