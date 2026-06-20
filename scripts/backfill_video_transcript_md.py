"""Backfill — render cleaned ``.md`` transcripts for existing video ``.vtt`` files.

影片清理稿 ``KB/Raw/Videos/{id}.md``（人讀 + ``/start-video`` ingest 的優先輸入）
原本只在抓字幕（watchlist confirm）時產生。本腳本替「在這功能上線前就已存在」的
``.vtt`` 補一份並排 ``.md``。冪等：預設跳過已存在的 ``.md``；``--all`` 強制重新
render（``.vtt`` 是唯一真相，重跑安全）。在擁有 vault 的主機（VPS）上跑。

    python -m scripts.backfill_video_transcript_md --dry-run   # 預覽
    python -m scripts.backfill_video_transcript_md             # 補缺的
    python -m scripts.backfill_video_transcript_md --all       # 全部重 render
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from shared.config import get_vault_path
from shared.video_transcript_writer import write_video_transcript_md

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def backfill(vault: Path, *, dry_run: bool, render_all: bool) -> int:
    raw_videos = vault / "KB" / "Raw" / "Videos"
    if not raw_videos.is_dir():
        print(f"(no {raw_videos} — nothing to backfill)")
        return 0

    created = skipped = failed = 0
    for vtt in sorted(raw_videos.glob("*.vtt")):
        video_id = vtt.stem
        if not _VIDEO_ID_RE.fullmatch(video_id):
            print(f"skip  {video_id!r}: not a video-id stem")
            skipped += 1
            continue
        md = raw_videos / f"{video_id}.md"
        if md.exists() and not render_all:
            print(f"skip  {video_id}: {video_id}.md already exists")
            skipped += 1
            continue

        print(f"{'PLAN ' if dry_run else 'render'} KB/Raw/Videos/{video_id}.md")
        if dry_run:
            created += 1
            continue
        try:
            out = write_video_transcript_md(vault, video_id)
            if out is None:
                print(f"  (skip {video_id}: empty / missing transcript)")
                failed += 1
            else:
                created += 1
        except Exception as exc:  # noqa: BLE001 — 單片失敗不中斷整批
            print(f"  FAILED {video_id}: {exc}")
            failed += 1

    verb = "would render" if dry_run else "rendered"
    print(f"\n{verb} {created}, skipped {skipped}, failed {failed}")
    return created


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="preview without writing")
    parser.add_argument(
        "--all",
        action="store_true",
        dest="render_all",
        help="re-render even if .md exists",
    )
    parser.add_argument("--vault", type=Path, default=None, help="vault root (default: config)")
    args = parser.parse_args()
    vault = args.vault or get_vault_path()
    print(f"vault: {vault}")
    backfill(vault, dry_run=args.dry_run, render_all=args.render_all)
    if args.dry_run:
        print("\n(dry-run — re-run without --dry-run to apply)")


if __name__ == "__main__":
    sys.exit(0 if (main() or True) else 1)
