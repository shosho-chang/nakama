#!/usr/bin/env python3
"""CLI helper for the textbook-ingest skill's ``--from-queue`` / ``--watch`` modes.

Usage::

    python queue_processor.py next
    python queue_processor.py mark <book_id> <status> [--chapters N] [--error MSG]
    python queue_processor.py watch [--interval N]

Exit codes::

    0  success
    1  next: queue is empty
    2  mark: invalid status or unknown book_id
"""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

# Allow ``import shared.*`` when invoked standalone (any cwd).
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _cmd_next() -> int:
    from shared.book_queue import next_queued

    book_id = next_queued()
    if book_id is None:
        return 1
    print(book_id)
    return 0


def _cmd_mark(book_id: str, status: str, chapters: int | None, error: str | None) -> int:
    from shared.book_queue import QueueStatusError, mark_status

    try:
        mark_status(book_id, status, chapters_done=chapters, error=error)
    except QueueStatusError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except LookupError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


def _cmd_watch(interval: int, _stop: threading.Event | None = None) -> int:
    """Poll the ingest queue every ``interval`` seconds and dispatch ingest jobs.

    ``_stop`` is an injectable threading.Event for tests; in production it is
    wired to SIGINT/SIGTERM so Ctrl+C lets the current ingest finish cleanly.
    """
    import signal
    import subprocess
    import time

    if _stop is None:
        _stop = threading.Event()

        def _handle_stop(sig, frame):
            print("\n[watch] stop signal received — waiting for current ingest.", flush=True)
            _stop.set()

        signal.signal(signal.SIGINT, _handle_stop)
        signal.signal(signal.SIGTERM, _handle_stop)

    idle_report_every = max(interval * 5, 300) if interval > 0 else 300
    last_idle_report = time.monotonic() - idle_report_every  # report on first idle

    print(
        f"[watch] polling every {interval}s — Ctrl+C to stop after current ingest.",
        flush=True,
    )

    while not _stop.is_set():
        from shared.book_queue import next_queued

        book_id = next_queued()
        if book_id is not None:
            last_idle_report = time.monotonic()
            print(f"[watch] dispatching ingest: {book_id}", flush=True)
            # start_new_session isolates the child from Ctrl+C so the ingest
            # finishes even when the user requests a graceful stop.
            proc = subprocess.Popen(
                ["claude", "-p", "/textbook-ingest --from-queue"],
                start_new_session=True,
            )
            proc.wait()
            print(f"[watch] ingest finished (exit {proc.returncode}) — polling again.", flush=True)
            continue

        now = time.monotonic()
        if now - last_idle_report >= idle_report_every:
            print(
                f"[watch] idle — queue empty, next check in {interval}s",
                flush=True,
            )
            last_idle_report = now

        _stop.wait(timeout=float(interval))

    print("[watch] stopped.", flush=True)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="queue_processor.py",
        description="Ingest queue CLI for the textbook-ingest skill.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("next", help="Print the next queued book_id (exit 1 if empty).")

    p_mark = sub.add_parser("mark", help="Update the status of a queued book.")
    p_mark.add_argument("book_id", help="Book ID to update.")
    p_mark.add_argument("status", help="New status value.")
    p_mark.add_argument("--chapters", type=int, default=None, help="chapters_done count.")
    p_mark.add_argument("--error", default=None, help="Error message (for failed status).")

    p_watch = sub.add_parser("watch", help="Watch queue and dispatch ingests automatically.")
    p_watch.add_argument(
        "--interval",
        type=int,
        default=60,
        metavar="N",
        help="Seconds between queue polls (default: 60).",
    )

    return parser


def main() -> int:
    args = _build_parser().parse_args()

    if args.command == "next":
        return _cmd_next()
    if args.command == "mark":
        return _cmd_mark(args.book_id, args.status, args.chapters, args.error)
    # args.command == "watch"
    return _cmd_watch(args.interval)


if __name__ == "__main__":
    sys.exit(main())
