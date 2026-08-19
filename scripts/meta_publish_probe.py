"""Supervised Meta Slice 0 probe.

``credentials`` is read-only.  Every publishing subcommand is a dry-run unless
``--execute`` is supplied explicitly.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.usopp.media_staging import MediaStager, MediaStagingConfig  # noqa: E402
from scripts.publish_dispatch import build_meta_client  # noqa: E402


def _files(values: list[str] | None) -> list[Path]:
    paths = [Path(value).resolve() for value in values or []]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise SystemExit("missing media file(s): " + ", ".join(missing))
    return paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Meta publishing capability probe")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("credentials", help="read-only Page + IG identity probe")
    for name in ("ig-reel", "fb-reel"):
        command = sub.add_parser(name)
        command.add_argument("--file", required=True)
        command.add_argument("--caption", required=True)
        command.add_argument("--execute", action="store_true")
    for name in ("ig-carousel", "fb-multi-photo"):
        command = sub.add_parser(name)
        command.add_argument("--file", action="append", required=True)
        command.add_argument("--caption", required=True)
        command.add_argument("--execute", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = args.command or "credentials"
    if command == "credentials":
        print(json.dumps(build_meta_client().credential_probe(), ensure_ascii=False, indent=2))
        return 0

    paths = _files([args.file] if isinstance(args.file, str) else args.file)
    if command in {"ig-carousel", "fb-multi-photo"} and len(paths) < 2:
        raise SystemExit(f"{command} requires at least two --file values")
    if not args.execute:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "command": command,
                    "files": [str(path) for path in paths],
                    "caption_chars": len(args.caption),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    client = build_meta_client()
    checkpoint: dict = {}

    def save(value: dict) -> None:
        checkpoint.update(value)

    staged = []
    stager = None
    try:
        if command == "ig-reel":
            stager = MediaStager(MediaStagingConfig.from_env())
            staged = stager.stage_files(paths)
            result = client.publish_instagram_reel(
                video_url=staged[0].url,
                caption=args.caption,
                checkpoint=checkpoint,
                save_checkpoint=save,
            )
        elif command == "fb-reel":
            result = client.publish_facebook_reel(
                video_path=paths[0],
                description=args.caption,
                checkpoint=checkpoint,
                save_checkpoint=save,
            )
        else:
            stager = MediaStager(MediaStagingConfig.from_env())
            staged = stager.stage_files(paths)
            if command == "ig-carousel":
                result = client.publish_instagram_carousel(
                    image_urls=[item.url for item in staged],
                    caption=args.caption,
                    checkpoint=checkpoint,
                    save_checkpoint=save,
                )
            else:
                result = client.publish_facebook_multi_photo(
                    image_urls=[item.url for item in staged],
                    message=args.caption,
                    checkpoint=checkpoint,
                    save_checkpoint=save,
                )
    finally:
        if stager is not None:
            stager.cleanup(item.key for item in staged)
    print(
        json.dumps(
            {"external_id": result.external_id, "permalink": result.permalink},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
