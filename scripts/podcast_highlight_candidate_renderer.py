"""Acquire, render, hydrate, or verify trusted Podcast Highlight candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.brook.script_video.highlight_candidate_renderer import (  # noqa: E402
    TrustedRenderError,
    hydrate_dp_proposal,
    hyperframes_runtime_status,
    prepare_hyperframes_runtime,
    render_hyperframes_candidate,
    verify_hyperframes_render_receipt,
)


def _json_file(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TrustedRenderError(f"{label} is not readable JSON: {path}") from error


def _file_identity(root: Path, path: Path) -> dict[str, object]:
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise TrustedRenderError("receipt path must be an episode-local file")
    raw = resolved.read_bytes()
    return {
        "path": resolved.relative_to(root).as_posix(),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Trusted HyperFrames candidate boundary. Rendering never downloads packages; "
            "run prepare-runtime explicitly during provisioning."
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)

    status = commands.add_parser("runtime-status", help="verify one preinstalled runtime")
    status.add_argument(
        "--package", required=True, choices=("hyperframes@0.6.42", "hyperframes@0.7.72")
    )
    status.add_argument("--runtime-root", type=Path)

    prepare = commands.add_parser(
        "prepare-runtime", help="explicitly acquire a pinned runtime (network-capable gate)"
    )
    prepare.add_argument(
        "--package", required=True, choices=("hyperframes@0.6.42", "hyperframes@0.7.72")
    )
    prepare.add_argument("--runtime-root", type=Path)

    render = commands.add_parser("render", help="render one registered spec into trusted media")
    render.add_argument("episode", type=Path)
    render.add_argument("--cut-id", required=True)
    render.add_argument("--revision-id", required=True)
    render.add_argument("--candidate-id", required=True)
    render.add_argument("--component", required=True)
    render.add_argument("--params", required=True, type=Path, help="closed render_params JSON")
    render.add_argument("--on-screen-text-file", required=True, type=Path)
    render.add_argument("--runtime-root", type=Path)

    hydrate = commands.add_parser(
        "hydrate-dp",
        help="join every spec-only DP row to trusted render/acquisition identities",
    )
    hydrate.add_argument("episode", type=Path)
    hydrate.add_argument("--cut-id", required=True)
    hydrate.add_argument("--revision-id", required=True)
    hydrate.add_argument("--attempt", required=True, type=int)
    hydrate.add_argument("--proposal", required=True, type=Path)
    hydrate.add_argument("--output", required=True, type=Path)
    hydrate.add_argument("--runtime-root", type=Path)

    verify = commands.add_parser("verify", help="freshly verify one trusted render receipt")
    verify.add_argument("episode", type=Path)
    verify.add_argument("--receipt", required=True, type=Path)
    verify.add_argument("--expected", required=True, type=Path, help="expected binding JSON")
    verify.add_argument("--runtime-root", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "runtime-status":
            result = hyperframes_runtime_status(args.package, runtime_root=args.runtime_root)
        elif args.command == "prepare-runtime":
            result = prepare_hyperframes_runtime(args.package, runtime_root=args.runtime_root)
        elif args.command == "render":
            params = _json_file(args.params, "render params")
            try:
                on_screen_text = args.on_screen_text_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as error:
                raise TrustedRenderError("on-screen text file is unreadable") from error
            result = render_hyperframes_candidate(
                args.episode,
                cut_id=args.cut_id,
                revision_id=args.revision_id,
                candidate_id=args.candidate_id,
                component=args.component,
                render_params=params,
                expected_on_screen_text=on_screen_text,
                runtime_root=args.runtime_root,
            )
        elif args.command == "hydrate-dp":
            result = hydrate_dp_proposal(
                args.episode,
                cut_id=args.cut_id,
                revision_id=args.revision_id,
                attempt=args.attempt,
                proposal_path=args.proposal,
                output_path=args.output,
                runtime_root=args.runtime_root,
            )
        else:
            root = args.episode.resolve()
            expected = _json_file(args.expected, "expected binding")
            if not isinstance(expected, dict):
                raise TrustedRenderError("expected binding must be a JSON object")
            result = verify_hyperframes_render_receipt(
                root,
                receipt_identity=_file_identity(root, args.receipt),
                expected_cut_id=expected.get("cut_id"),
                expected_revision_id=expected.get("revision_id"),
                expected_candidate_id=expected.get("candidate_id"),
                expected_component=expected.get("component"),
                expected_render_params=expected.get("render_params"),
                expected_on_screen_text=expected.get("on_screen_text"),
                expected_media=expected.get("media"),
                runtime_root=args.runtime_root,
            )
    except TrustedRenderError as error:
        parser.exit(2, f"trusted candidate gate failed: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
