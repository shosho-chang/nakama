#!/usr/bin/env python3
"""Validate, emit, or explicitly transaction-apply mutable long-highlight state."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agents.brook.script_video.long_highlight_materializer import (  # noqa: E402
    ResolveScriptingAdapter,
    apply_preview,
    commit_transaction,
    emit_recipes,
    rollback_transaction,
    supersede_stale_transaction,
    validate_projection,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("emit-recipes", "validate"):
        command = commands.add_parser(name)
        command.add_argument("episode", type=Path)
        command.add_argument("--cut-id", required=True)
        command.add_argument("--state", required=True, type=Path)
        command.add_argument("--output-dir", type=Path)
    apply_command = commands.add_parser(
        "apply-preview", help="duplicate-swap, apply approved recipes, render and probe preview"
    )
    apply_command.add_argument("episode", type=Path)
    apply_command.add_argument("--cut-id", required=True)
    apply_command.add_argument("--state", required=True, type=Path)
    apply_command.add_argument("--canonical-name", required=True)
    apply_command.add_argument("--canonical-uid", required=True)
    apply_command.add_argument("--preview", required=True, type=Path)
    apply_command.add_argument("--transaction", type=Path)
    for name in ("commit", "rollback", "supersede-stale"):
        command = commands.add_parser(name)
        command.add_argument("episode", type=Path)
        command.add_argument("--transaction", required=True, type=Path)
        if name == "supersede-stale":
            command.add_argument("--active-name")
            command.add_argument("--active-uid")
            command.add_argument("--backup-name")
            command.add_argument("--backup-uid")
    return parser


def _load_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"state is not readable JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"state must be a JSON object: {path}")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "emit-recipes":
        state = _load_state(args.state)
        result = emit_recipes(
            args.episode,
            args.cut_id,
            state,
            output_dir=args.output_dir,
        )
    elif args.command == "validate":
        state = _load_state(args.state)
        result = validate_projection(
            args.episode,
            args.cut_id,
            state,
            output_dir=args.output_dir,
        )
    else:
        state = None
        if args.command == "apply-preview":
            state = _load_state(args.state)
            # Keep invalid mutable state outside the Resolve host boundary.
            validate_projection(args.episode, args.cut_id, state)
        adapter = ResolveScriptingAdapter(args.episode)
        if args.command == "apply-preview":
            result = apply_preview(
                args.episode,
                args.cut_id,
                state,
                canonical_name=args.canonical_name,
                canonical_uid=args.canonical_uid,
                preview_path=args.preview,
                transaction_path=args.transaction,
                adapter=adapter,
            )
        elif args.command == "commit":
            result = commit_transaction(args.transaction, adapter=adapter)
        elif args.command == "rollback":
            result = rollback_transaction(args.transaction, adapter=adapter)
        else:
            explicit = [
                args.active_name,
                args.active_uid,
                args.backup_name,
                args.backup_uid,
            ]
            if any(explicit) and not all(explicit):
                raise ValueError("legacy supersede requires active/backup name and UID together")
            result = supersede_stale_transaction(
                args.transaction,
                adapter=adapter,
                active=(
                    {"name": args.active_name, "uid": args.active_uid} if all(explicit) else None
                ),
                backup=(
                    {"name": args.backup_name, "uid": args.backup_uid} if all(explicit) else None
                ),
            )
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
