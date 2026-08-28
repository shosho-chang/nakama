#!/usr/bin/env python3
"""CLI for the mutable Stage 5 long-highlight orchestrator.

DirectoryStageRunner is the host exchange adapter: it writes stage request JSON,
reads host-supplied response JSON, and never starts a worker process or network call.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agents.brook.script_video.long_highlight_orchestrator import (  # noqa: E402
    DirectoryStageRunner,
    LongHighlightOrchestrator,
    SourceInput,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    start = commands.add_parser("start", help="create a mutable run and optionally start it")
    start.add_argument("state", type=Path)
    start.add_argument("--episode-id", required=True)
    start.add_argument("--srt", required=True, type=Path)
    start.add_argument("--media", required=True, type=Path)
    start.add_argument("--context-ref", action="append", default=[])
    start.add_argument("--exchange-dir", type=Path)
    start.add_argument("--dry-run", action="store_true")

    status = commands.add_parser("status", help="print current run state")
    status.add_argument("state", type=Path)

    resume = commands.add_parser("resume", help="continue only unfinished stages")
    resume.add_argument("state", type=Path)
    resume.add_argument("--exchange-dir", type=Path)

    approve = commands.add_parser("approve", help="approve a candidate and continue")
    approve.add_argument("state", type=Path)
    approve.add_argument("--candidate", required=True)
    approve.add_argument("--corrections", type=Path)
    approve.add_argument("--exchange-dir", type=Path)

    retry = commands.add_parser("retry-event", help="retry exactly one failed event")
    retry.add_argument("state", type=Path)
    retry.add_argument("--stage", required=True)
    retry.add_argument("--event-id", required=True)
    retry.add_argument("--exchange-dir", type=Path)

    adopt = commands.add_parser("adopt-existing", help="import existing semantic draft rows")
    adopt.add_argument("state", type=Path)
    adopt.add_argument("--director", type=Path)
    adopt.add_argument("--dp", type=Path)
    adopt.add_argument("--exchange-dir", type=Path)

    adopt_winner = commands.add_parser(
        "adopt-winner", help="import an already human-approved winner"
    )
    adopt_winner.add_argument("state", type=Path)
    adopt_winner.add_argument("--winner", required=True, type=Path)
    adopt_winner.add_argument("--tighten-ref", type=Path)
    adopt_winner.add_argument("--director", type=Path)
    adopt_winner.add_argument("--dp", type=Path)
    adopt_winner.add_argument("--exchange-dir", type=Path)
    return parser


def _runner(state_path: Path, exchange_dir: Path | None) -> DirectoryStageRunner:
    return DirectoryStageRunner(exchange_dir or state_path.parent / "long-highlight-exchange")


def _read_object(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _print(value: Any) -> None:
    # Keep stdout portable across Windows consoles whose default codec is cp1252.
    # State and exchange files remain UTF-8; only terminal JSON uses escapes.
    print(json.dumps(value, ensure_ascii=True, indent=2))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "start":
        orchestrator = LongHighlightOrchestrator.create(
            args.state,
            SourceInput(
                episode_id=args.episode_id,
                srt_path=args.srt,
                media_path=args.media,
                context_refs=tuple(args.context_ref),
            ),
            _runner(args.state, args.exchange_dir),
        )
        result = orchestrator.dry_run() if args.dry_run else orchestrator.resume()
    elif args.command == "status":
        result = LongHighlightOrchestrator.load(args.state, _runner(args.state, None)).status()
    elif args.command == "resume":
        result = LongHighlightOrchestrator.load(
            args.state, _runner(args.state, args.exchange_dir)
        ).resume()
    elif args.command == "approve":
        result = LongHighlightOrchestrator.load(
            args.state, _runner(args.state, args.exchange_dir)
        ).approve_winner(args.candidate, _read_object(args.corrections))
    elif args.command == "retry-event":
        result = LongHighlightOrchestrator.load(
            args.state, _runner(args.state, args.exchange_dir)
        ).retry_event(args.stage, args.event_id)
    elif args.command == "adopt-existing":
        result = LongHighlightOrchestrator.load(
            args.state, _runner(args.state, args.exchange_dir)
        ).adopt_existing(
            director=_read_object(args.director),
            dp=_read_object(args.dp),
        )
    else:
        orchestrator = LongHighlightOrchestrator.load(
            args.state, _runner(args.state, args.exchange_dir)
        )
        if args.director is not None or args.dp is not None:
            orchestrator.adopt_existing(
                director=_read_object(args.director),
                dp=_read_object(args.dp),
            )
        winner = _read_object(args.winner)
        if winner is None:  # argparse requires this; keeps the type boundary explicit
            raise ValueError("winner JSON is required")
        result = orchestrator.adopt_winner(winner, tighten_ref=args.tighten_ref)
    _print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
