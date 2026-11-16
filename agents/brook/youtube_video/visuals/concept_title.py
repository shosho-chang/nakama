"""第 2 類「重點概念 title」：把規劃表上核准的列渲成透明 overlay 素材。

設計（修修 2026-11-15 定版）見
`video/compositions/concept_title/compositions/concept_title.html`：
白色字牌黑字、背後一條品牌碎片 pattern；碎片條有四種「上下平行、左右各一個斜度」的形狀，
每張卡隨機挑一種——用列 id 決定，同一列重渲永遠同一種，不會每次換。

時間：從這一列蓋住的第一句字幕開始，停留＝那幾句的總長，但夾在 1.5–4 秒之間
（舊片 keyword 字卡多半 2–4 秒；太短看不完，太長變成壁紙）。

用法：

    python -m agents.brook.youtube_video.visuals.concept_title "G:/Footages/<episode>"
    python -m agents.brook.youtube_video.visuals.concept_title "G:/Footages/<episode>" \\
        --row <id> --force
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import unicodedata
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from agents.brook.youtube_video import visual_plan as vp
from agents.brook.youtube_video.visuals import delivery
from shared.quiet_subprocess import quiet_kwargs

CATEGORY = "concept_title"
COMPONENT = "concept_title"
REPO_ROOT = Path(__file__).resolve().parents[4]
COMPOSITION_DIR = REPO_ROOT / "video" / "compositions" / "concept_title"
COMPOSITION_HTML = COMPOSITION_DIR / "compositions" / "concept_title.html"
HYPERFRAMES = "hyperframes@0.7.72"

MIN_SEC = 1.5
MAX_SEC = 4.0
MAX_WIDTH_UNITS = 12  # 96px 一行：全形字算 1、半形算 0.55，12 個全形字約 1450px 寬（含碎片條）
SHAPES = (1, 2, 3, 4)

Runner = Callable[[list[str], Path], None]


def text_width_units(text: str) -> float:
    return sum(1.0 if unicodedata.east_asian_width(ch) in ("W", "F") else 0.55 for ch in text)


def shape_for(row_id: str) -> int:
    digest = hashlib.sha256(row_id.encode("utf-8")).digest()
    return SHAPES[digest[0] % len(SHAPES)]


def show_seconds(span_sec: float) -> float:
    return round(min(MAX_SEC, max(MIN_SEC, span_sec)), 3)


def card_text(row: Mapping[str, Any]) -> str:
    text = (row.get("onscreen_text") or "").strip()
    if not text:
        raise delivery.DeliveryError(f"{row['id']}: 「畫面上的字」是空的")
    if "\n" in text:
        raise delivery.DeliveryError(f"{row['id']}: 重點概念 title 只能一行：{text!r}")
    if text_width_units(text) > MAX_WIDTH_UNITS:
        raise delivery.DeliveryError(
            f"{row['id']}: 「{text}」太長（上限約 {MAX_WIDTH_UNITS} 個全形字），請在 Bridge 縮短"
        )
    return text


def card_variables(plan: Mapping[str, Any], row: Mapping[str, Any]) -> tuple[dict, float, float]:
    if row.get("category") != CATEGORY:
        raise delivery.DeliveryError(f"{row.get('id')}: 不是重點概念 title")
    start, span = delivery.overlay_window(plan, row)
    seconds = show_seconds(span)
    variables = {"text": card_text(row), "shape": shape_for(row["id"]), "show_sec": seconds}
    return variables, start, seconds


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


def _render(
    variables: Mapping[str, Any], seconds: float, paths: delivery.AssetPaths, runner: Runner
) -> None:
    paths.media.parent.mkdir(parents=True, exist_ok=True)
    raw = paths.media.with_name(paths.media.stem + ".full.mov")
    vars_file = paths.media.with_name(paths.media.stem + ".vars.json")
    vars_file.write_text(json.dumps(variables, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        runner(
            [
                "npx",
                "--yes",
                HYPERFRAMES,
                "render",
                ".",
                "-c",
                "compositions/concept_title.html",
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
        # 元件固定渲 8 秒；交件長度＝停留秒數。ProRes 每格都是關鍵格，copy 截斷是逐格精準的。
        runner(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(raw),
                "-t",
                f"{seconds:.3f}",
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
    variables, start, seconds = card_variables(plan, row)
    component_sha = delivery.file_sha(COMPOSITION_HTML)
    sha = delivery.inputs_sha(
        category=CATEGORY, component=COMPONENT, component_sha=component_sha, variables=variables
    )
    paths = delivery.asset_paths(episode_dir, row["id"])
    if not force and delivery.is_current(paths, sha):
        return delivery.load_receipt(paths.receipt) or {}, False
    _render(variables, seconds, paths, runner)
    receipt = delivery.write_receipt(
        paths,
        row=row,
        component=COMPONENT,
        component_sha=component_sha,
        variables=variables,
        start_sec=start,
        duration_sec=seconds,
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
    parser = argparse.ArgumentParser(description="渲重點概念 title（第 2 類）")
    parser.add_argument("episode", type=Path, help="集數資料夾（含 visual_plan.json）")
    parser.add_argument("--row", help="只渲這一列")
    parser.add_argument("--force", action="store_true", help="收據對得上也重渲")
    args = parser.parse_args(argv)

    plan = vp.load_plan(vp.plan_path(args.episode))
    rows = approved_rows(plan)
    if args.row:
        rows = [row for row in rows if row["id"] == args.row]
        if not rows:
            print(f"找不到核准的重點概念 title：{args.row}", file=sys.stderr)
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
            f"{label} {row['id']}：「{receipt['variables']['text']}」"
            f" {receipt['start_sec']:.2f}s 起 {receipt['duration_sec']:.2f}s"
            f" 形狀 {receipt['variables']['shape']}"
        )
    print(f"重點概念 title：{len(rows) - failures}/{len(rows)} 張完成")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
