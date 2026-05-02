"""CLI entry point for the Script-Driven Video Production pipeline.

Usage:
    python -m agents.brook.script_video --episode <episode-id>
    python -m agents.brook.script_video --episode ep-001 --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m agents.brook.script_video",
        description="Script-Driven Video Production pipeline",
    )
    parser.add_argument(
        "--episode",
        required=True,
        metavar="ID",
        help="Episode ID (subdirectory of data/script_video/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs without running the full pipeline",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    from agents.brook.script_video import pipeline

    if args.dry_run:
        paths = pipeline._EpisodePaths(args.episode)
        try:
            paths.validate()
            print(f"Episode '{args.episode}': inputs OK")
        except FileNotFoundError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        return

    try:
        result = pipeline.run(args.episode)
        print(f"Done: {result.fcpxml_path}")
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        logging.exception("Pipeline failed: %s", exc)
        sys.exit(2)


if __name__ == "__main__":
    main()
