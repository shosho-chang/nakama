"""One-off migration — move YouTube transcripts into the unified raw layer.

ADR-046 Slice 0A: video transcripts used to live under the Watchlist entry
dir (``Watchlist/youtube/{video_id}/transcript.vtt``), out of step with
articles (``KB/Raw/Articles/{slug}.md``). This moves each transcript to the
canonical raw home ``KB/Raw/Videos/{video_id}.vtt`` so all three sources'
raw content shares one layer. The manifest (``manifest.json``) stays in
``Watchlist/youtube/{video_id}/`` as the "videos I've added" registry.

Idempotent: skips entries whose transcript is already migrated (or absent).
Run on the host that owns the vault (VPS), BEFORE restarting the web service,
so the resolver's new path (``KB/Raw/Videos/...``) always has a file behind it.

    python -m scripts.migrate_video_transcripts_to_raw --dry-run   # preview
    python -m scripts.migrate_video_transcripts_to_raw             # apply
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

from shared.config import get_vault_path

# Mirror shared.reading_source_registry._VALID_YOUTUBE_ID — only real video-id
# dirs are migrated; stray/hidden dirs under Watchlist/youtube/ are skipped.
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def migrate(vault: Path, *, dry_run: bool) -> int:
    watchlist_root = vault / "Watchlist" / "youtube"
    raw_videos = vault / "KB" / "Raw" / "Videos"

    if not watchlist_root.is_dir():
        print(f"(no {watchlist_root} — nothing to migrate)")
        return 0

    if not dry_run:
        raw_videos.mkdir(parents=True, exist_ok=True)

    moved = skipped = 0
    for entry_dir in sorted(watchlist_root.iterdir()):
        if not entry_dir.is_dir():
            continue
        video_id = entry_dir.name
        if not _VIDEO_ID_RE.fullmatch(video_id):
            print(f"skip  {video_id!r}: not a video-id dir")
            skipped += 1
            continue
        old = entry_dir / "transcript.vtt"
        new = raw_videos / f"{video_id}.vtt"

        if new.exists():
            print(f"skip  {video_id}: already at KB/Raw/Videos/{video_id}.vtt")
            skipped += 1
            continue
        if not old.is_file():
            print(f"skip  {video_id}: no transcript.vtt under Watchlist")
            skipped += 1
            continue

        print(f"{'PLAN ' if dry_run else 'move '} {old}  ->  KB/Raw/Videos/{video_id}.vtt")
        if not dry_run:
            shutil.move(str(old), str(new))
        moved += 1

    verb = "would move" if dry_run else "moved"
    print(f"\n{verb} {moved}, skipped {skipped}")
    return moved


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="preview without moving")
    parser.add_argument("--vault", type=Path, default=None, help="vault root (default: config)")
    args = parser.parse_args()
    vault = args.vault or get_vault_path()
    print(f"vault: {vault}")
    migrate(vault, dry_run=args.dry_run)
    if args.dry_run:
        print("\n(dry-run — re-run without --dry-run to apply)")


if __name__ == "__main__":
    sys.exit(0 if (main() or True) else 1)
