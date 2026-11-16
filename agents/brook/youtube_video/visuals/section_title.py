"""稿上 `###` 的「小標題遮罩」：把規劃表上核准的小標題列渲成透明 overlay 素材。

設計（修修 2026-11-16 定版）見 `video/compositions/section_title/compositions/section_title.html`：
全畫面深色半透明遮罩，只有左上、右下兩角探進幾塊品牌手冊第 24 頁的橘色碎片（右下是原稿，
左上是同一組沿對角線翻面），白色標題一個字一個字浮出。比章節卡輕——章節卡是滿版插入、碎片圍整圈；
這裡疊在錄影上、不插入時間，因為修修講話不會停下來（規格 §3.4）。
（repo 裡另有舊的 `video/compositions/transition_title/transition_title_wide.html` `style=scrim`，
是長精華的紙卡系統，保留不動，不是這個。）

時間：從這一列的第一句字幕開始，固定 2.5 秒（2.2 秒起退場）。

標題＝「畫面上的字」（`###` 的文字），一行；字數多時自動縮小字級，太長就擋下。

用法：

    python -m agents.brook.youtube_video.visuals.section_title "G:/Footages/<episode>"
    python -m agents.brook.youtube_video.visuals.section_title "G:/Footages/<episode>" \\
        --row <id> --force
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import unicodedata
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from agents.brook.youtube_video import visual_plan as vp
from agents.brook.youtube_video.visuals import delivery
from shared.quiet_subprocess import quiet_kwargs

CATEGORY = "section_title"
COMPONENT = "section_title"
REPO_ROOT = Path(__file__).resolve().parents[4]
COMPOSITION_DIR = REPO_ROOT / "video" / "compositions" / "section_title"
COMPOSITION_HTML = COMPOSITION_DIR / "compositions" / "section_title.html"
HYPERFRAMES = "hyperframes@0.7.72"

SHOW_SEC = 2.5  # 元件本身就是 2.5 秒（75 格），不用再截
MAX_FONT_PX = 96
MIN_FONT_PX = 72
# 兩角碎片停住後，標題那一條（y≈392–488）在 x≈140–1780 之間是空的；留邊後取 1400px
TITLE_MAX_WIDTH_PX = 1400

Runner = Callable[[list[str], Path], None]


def text_width_units(text: str) -> float:
    return sum(1.0 if unicodedata.east_asian_width(ch) in ("W", "F") else 0.55 for ch in text)


def section_title(row: Mapping[str, Any]) -> str:
    text = (row.get("onscreen_text") or "").strip()
    if not text:
        raise delivery.DeliveryError(f"{row['id']}: 小標題是空的")
    if "\n" in text:
        raise delivery.DeliveryError(f"{row['id']}: 小標題只能一行：{text!r}")
    return text


def font_px_for(title: str) -> int:
    """字數多就縮小字級，讓整行放在兩角碎片之間；縮到 72px 還放不下就擋下。"""
    units = text_width_units(title)
    size = min(MAX_FONT_PX, math.floor(TITLE_MAX_WIDTH_PX / max(units, 1)))
    if size < MIN_FONT_PX:
        raise delivery.DeliveryError(
            f"小標題「{title}」太長（{MIN_FONT_PX}px 也放不下一行），請在稿上縮短 ### 的文字"
        )
    return size


def card_variables(plan: Mapping[str, Any], row: Mapping[str, Any]) -> tuple[dict, float]:
    if row.get("category") != CATEGORY:
        raise delivery.DeliveryError(f"{row.get('id')}: 不是小標題遮罩")
    start, _span = delivery.overlay_window(plan, row)
    title = section_title(row)
    try:
        size = font_px_for(title)
    except delivery.DeliveryError as exc:
        raise delivery.DeliveryError(f"{row['id']}: {exc}") from None
    return {"title": title, "font_px": size}, start


def _hyperframes_render(argv: list[str], cwd: Path) -> None:
    proc = subprocess.run(
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=sys.platform == "win32",  # npx 在 Windows 是 .cmd
        **quiet_kwargs(),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"render 失敗：{(proc.stderr or proc.stdout or '')[-600:]}")


def _render(variables: Mapping[str, Any], paths: delivery.AssetPaths, runner: Runner) -> None:
    paths.media.parent.mkdir(parents=True, exist_ok=True)
    raw = paths.media.with_name(paths.media.stem + ".full.mov")
    vars_file = paths.media.with_name(paths.media.stem + ".vars.json")
    vars_file.write_text(json.dumps(variables, ensure_ascii=False), encoding="utf-8")
    try:
        runner(
            [
                "npx",
                "--yes",
                HYPERFRAMES,
                "render",
                ".",
                "-c",
                "compositions/section_title.html",
                "-o",
                str(raw),
                "--format",
                "mov",
                "--fps",
                delivery.FPS,
                "-q",
                "standard",
                "--quiet",
                "--no-browser-gpu",
                "--variables-file",
                str(vars_file),
            ],
            COMPOSITION_DIR,
        )
        os.replace(raw, paths.media)  # 渲完整支才換上，失敗不會留半支在交件位置
    finally:
        raw.unlink(missing_ok=True)
        vars_file.unlink(missing_ok=True)


def deliver_row(
    episode_dir: Path,
    plan: Mapping[str, Any],
    row: Mapping[str, Any],
    *,
    force: bool = False,
    runner: Runner = _hyperframes_render,
) -> tuple[dict[str, Any], bool]:
    """渲一列並寫收據。回傳（收據, 這次有沒有真的渲）。"""
    variables, start = card_variables(plan, row)
    component_sha = delivery.file_sha(COMPOSITION_HTML)
    sha = delivery.inputs_sha(
        category=CATEGORY, component=COMPONENT, component_sha=component_sha, variables=variables
    )
    paths = delivery.asset_paths(episode_dir, row["id"])
    if not force and delivery.is_current(paths, sha):
        return delivery.load_receipt(paths.receipt) or {}, False
    _render(variables, paths, runner)
    receipt = delivery.write_receipt(
        paths,
        row=row,
        component=COMPONENT,
        component_sha=component_sha,
        variables=variables,
        start_sec=start,
        duration_sec=SHOW_SEC,
        sha=sha,
    )
    return receipt, True


def approved_rows(plan: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [
        row
        for row in plan.get("rows", [])
        if row.get("category") == CATEGORY and row.get("status") == "approved"
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="渲小標題遮罩（稿上 ###）")
    parser.add_argument("episode", type=Path, help="集數資料夾（含 visual_plan.json）")
    parser.add_argument("--row", help="只渲這一列")
    parser.add_argument("--force", action="store_true", help="收據對得上也重渲")
    args = parser.parse_args(argv)

    plan = vp.load_plan(vp.plan_path(args.episode))
    rows = approved_rows(plan)
    if args.row:
        rows = [row for row in rows if row["id"] == args.row]
        if not rows:
            print(f"找不到核准的小標題遮罩：{args.row}", file=sys.stderr)
            return 2
    failures = 0
    for row in rows:
        try:
            receipt, rendered = deliver_row(args.episode, plan, row, force=args.force)
        except delivery.DeliveryError as exc:
            failures += 1
            print(f"✗ {exc}", file=sys.stderr)
            continue
        label = "渲好" if rendered else "沿用"
        print(
            f"{label} {row['id']}：「{receipt['variables']['title']}」"
            f" {receipt['start_sec']:.2f}s 起 {receipt['duration_sec']:.2f}s，"
            f"{receipt['variables']['font_px']}px"
        )
    print(f"小標題遮罩：{len(rows) - failures}/{len(rows)} 張完成")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
