#!/usr/bin/env python3
"""render_still.py — thumbnail_worker 的 CLI 薄殼（skill 不重新發明 render）。

    python render_still.py --recipe youtube_host --title-hook "..." \
        --host-cutout <png> --out <png> [--accent "..."] [--palette palette.json]

    python render_still.py --recipe podcast --title-hook "..." \
        --host-cutout <png> --guest-cutout <png> --out <png>

visual_recipe routing 的 fail-loud 分支也在這裡（youtube_book — ADR-054 D2）。
背景層固定純色/漸層（composition CSS 內建；Unsplash PR5 未落地 — ADR-054 附錄 B）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

SUPPORTED_RECIPES = ("podcast", "youtube_host")


def ensure_recipe_supported(visual_recipe: str) -> None:
    """youtube_book 尚未實作 — fail loud 附操作指引（ADR-054 D2）。"""
    if visual_recipe == "youtube_book":
        raise NotImplementedError(
            "visual_recipe=youtube_book 尚未實作（修修＋書封同框需要參考圖庫）。"
            "請先把構圖參考圖放到 vault Attachments/cutouts/reference/youtube_book/ "
            "並開 issue 排 composition 實作；本次 packaging 先以 youtube_host 配方跑或跳過封面。"
        )
    if visual_recipe not in SUPPORTED_RECIPES:
        raise ValueError(
            f"unknown visual_recipe: {visual_recipe!r} (supported: {SUPPORTED_RECIPES})"
        )


async def _render(args: argparse.Namespace) -> Path:
    from agents.brook.script_video.render_workers.thumbnail_worker import (
        render_podcast_still,
        render_youtube_still,
    )

    palette = json.loads(args.palette.read_text(encoding="utf-8")) if args.palette else None
    if args.recipe == "podcast":
        if args.guest_cutout is None:
            raise ValueError("podcast 配方需要 --guest-cutout")
        return await render_podcast_still(
            title_hook=args.title_hook,
            host_cutout_path=args.host_cutout,
            guest_cutout_path=args.guest_cutout,
            out_png=args.out,
            accent_decoration=args.accent,
            palette=palette,
        )
    return await render_youtube_still(
        title_hook=args.title_hook,
        cutout_path=args.host_cutout,
        out_png=args.out,
        accent_decoration=args.accent,
        palette=palette,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipe", required=True)
    parser.add_argument("--title-hook", required=True)
    parser.add_argument("--host-cutout", type=Path, required=True)
    parser.add_argument("--guest-cutout", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--accent", default="")
    parser.add_argument("--palette", type=Path, help="palette JSON（keys: bg/fg/accent/bg_darken）")
    args = parser.parse_args()

    ensure_recipe_supported(args.recipe)
    out = asyncio.run(_render(args))
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
