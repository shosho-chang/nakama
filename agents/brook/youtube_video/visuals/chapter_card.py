"""第 1 類「章節卡」：把規劃表上核准的章節卡列渲成滿版插入素材（帶 alpha）。

設計（修修 2026-11-15 定版）見 `video/compositions/chapter_card/compositions/chapter_card.html`：
框取自品牌手冊第 24 頁「影片開頭」的向量原稿，橘色大長方形從畫面外圍一塊塊衝進來圍成一整圈，
標題一個字一個字浮現；最後 0.3 秒「炸開」——框往外飛出、灰底淡掉，露出下一段錄影。
（repo 裡另有舊的 `video/compositions/transition_title/`，是舊 LINE Seed 紙卡系統，
長精華還在用，不是這個。）

時間：稿上每個 `##` 一張，插在那一節第一句字幕之前，固定 2 秒（規格 §3.4「插入約 2 秒」）。
**前 1.7 秒滿版、最後 0.3 秒疊在下一段錄影上**——鋪時間軸時，下一段錄影要從章節卡第 1.7 秒開始，
所以收據多記 `insert_sec`（把錄影往後推幾秒＝1.7）與 `overlap_next_sec`（0.3）。

標題＝「畫面上的字」（`##` 的文字），一行；字數多時自動縮小字級，太長就擋下。

用法：

    python -m agents.brook.youtube_video.visuals.chapter_card "G:/Footages/<episode>"
    python -m agents.brook.youtube_video.visuals.chapter_card "G:/Footages/<episode>" \\
        --row <id> --force
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import unicodedata
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from agents.brook.youtube_video import visual_plan as vp
from agents.brook.youtube_video.visuals import delivery
from shared.quiet_subprocess import quiet_kwargs

CATEGORY = "chapter_card"
COMPONENT = "chapter_card"
REPO_ROOT = Path(__file__).resolve().parents[4]
COMPOSITION_DIR = REPO_ROOT / "video" / "compositions" / "chapter_card"
COMPOSITION_HTML = COMPOSITION_DIR / "compositions" / "chapter_card.html"
HYPERFRAMES = "hyperframes@0.7.72"

CARD_SEC = 2.0
OVERLAP_NEXT_SEC = 0.3  # 炸開退場：最後 0.3 秒疊在下一段錄影上
MAX_FONT_PX = 136
MIN_FONT_PX = 88
TITLE_MAX_WIDTH_PX = 1240  # 框內灰底中間能放字的寬度（左右都避開碎片）

Runner = Callable[[list[str], Path], None]


def text_width_units(text: str) -> float:
    return sum(1.0 if unicodedata.east_asian_width(ch) in ("W", "F") else 0.55 for ch in text)


def chapter_title(row: Mapping[str, Any]) -> str:
    text = (row.get("onscreen_text") or "").strip()
    if not text:
        raise delivery.DeliveryError(f"{row['id']}: 章節標題是空的")
    if "\n" in text:
        raise delivery.DeliveryError(f"{row['id']}: 章節標題只能一行：{text!r}")
    return text


def font_px_for(title: str) -> int:
    """字數多就縮小字級，讓整行放得進框內；縮到 88px 還放不下就擋下。"""
    units = text_width_units(title)
    size = min(MAX_FONT_PX, math.floor(TITLE_MAX_WIDTH_PX / max(units, 1)))
    if size < MIN_FONT_PX:
        raise delivery.DeliveryError(
            f"章節標題「{title}」太長（{MIN_FONT_PX}px 也放不下一行），請在稿上縮短 ## 的文字"
        )
    return size


def card_variables(plan: Mapping[str, Any], row: Mapping[str, Any]) -> tuple[dict, float]:
    if row.get("category") != CATEGORY:
        raise delivery.DeliveryError(f"{row.get('id')}: 不是章節卡")
    start = delivery.insert_point(plan, row)
    title = chapter_title(row)
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
                "compositions/chapter_card.html",
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
        # 交件長度固定 2 秒（逐格精準截斷；ProRes 每格都是關鍵格）
        runner(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(raw),
                "-t",
                f"{CARD_SEC:.3f}",
                "-c",
                "copy",
                str(paths.media),
            ],
            COMPOSITION_DIR,
        )
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
        duration_sec=CARD_SEC,
        sha=sha,
        extra={
            "insert_sec": round(CARD_SEC - OVERLAP_NEXT_SEC, 3),
            "overlap_next_sec": OVERLAP_NEXT_SEC,
        },
    )
    return receipt, True


def approved_rows(plan: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [
        row
        for row in plan.get("rows", [])
        if row.get("category") == CATEGORY and row.get("status") == "approved"
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="渲章節卡（第 1 類）")
    parser.add_argument("episode", type=Path, help="集數資料夾（含 visual_plan.json）")
    parser.add_argument("--row", help="只渲這一列")
    parser.add_argument("--force", action="store_true", help="收據對得上也重渲")
    args = parser.parse_args(argv)

    plan = vp.load_plan(vp.plan_path(args.episode))
    rows = approved_rows(plan)
    if args.row:
        rows = [row for row in rows if row["id"] == args.row]
        if not rows:
            print(f"找不到核准的章節卡：{args.row}", file=sys.stderr)
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
            f" 插在 {receipt['start_sec']:.2f}s 前，{receipt['variables']['font_px']}px"
        )
    print(f"章節卡：{len(rows) - failures}/{len(rows)} 張完成")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
