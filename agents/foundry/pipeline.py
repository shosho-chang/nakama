"""foundry pipeline entry — orchestrates planning / dispatching / FCPXML emit.

Subcommands (Phase 1 surface):
- plan: SRT → storyboard.yaml (PR-3 implements)
- render: storyboard.yaml → b_roll_*.mp4 (PR-4 implements)
- emit: storyboard.yaml + rendered mp4s → episode.fcpxml (PR-4 implements)
- run: plan → render → emit end-to-end

PR-1 ships the entry shell + arg parsing only. Each subcommand body raises
NotImplementedError pointing at the implementing PR.
"""

from __future__ import annotations

import argparse
import sys


def _cmd_plan(args: argparse.Namespace) -> int:
    raise NotImplementedError("PR-3 — planner.plan_episode + storyboard schema")


def _cmd_render(args: argparse.Namespace) -> int:
    raise NotImplementedError("PR-4 — render_dispatcher + hyperframes_worker")


def _cmd_emit(args: argparse.Namespace) -> int:
    raise NotImplementedError("PR-4 — fcpxml_emitter")


def _cmd_run(args: argparse.Namespace) -> int:
    raise NotImplementedError("PR-3/PR-4 — end-to-end orchestration")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agents.foundry", description=__doc__.splitlines()[0])
    p.add_argument("--episode", required=True, help="episode id under data/script_video/")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan").set_defaults(fn=_cmd_plan)
    sub.add_parser("render").set_defaults(fn=_cmd_render)
    sub.add_parser("emit").set_defaults(fn=_cmd_emit)
    sub.add_parser("run").set_defaults(fn=_cmd_run)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
