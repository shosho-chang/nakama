"""第 8 類「金句卡」：把規劃表上核准的金句列渲成透明 overlay 素材（錄影壓暗＋白色大字）。

設計（修修 2026-11-16 定版）見 `video/compositions/quote_dim/compositions/quote_dim.html`：
整個錄影壓暗，左上橘色引號，白色大字靠左一行一行滑入，下方橘色出處；右下角從底邊探進
品牌手冊第 24 頁的 3 塊橘色碎片，只露一截。
（repo 裡另有舊的 `video/compositions/quote_card/`，是長精華的 LINE Seed 紙卡系統，
保留不動，不是這個。）

字：「畫面上的字」（`onscreen_text`）＝金句原話。可以自己用換行分行；沒分的長句自動在標點後換行。
最多 3 行，每行約 15 個全形字；再長就擋下，請在 Bridge 縮短或拆成兩張。
出處：「出處」（`source`）一行，例：`Morgan Housel《致富心態》`；可留空；不能是網址。

時間：從這一列蓋住的第一句字幕開始，停留＝那幾句字幕的總長，夾在 4–9 秒之間
（修修過去成片的金句卡 p50 5.4–8.6 秒，讀字要駐留）。

用法：

    python -m agents.brook.youtube_video.visuals.quote_card "G:/Footages/<episode>"
    python -m agents.brook.youtube_video.visuals.quote_card "G:/Footages/<episode>" \\
        --row <id> --force
"""

from __future__ import annotations

import argparse
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

CATEGORY = "quote_card"
COMPONENT = "quote_dim"
REPO_ROOT = Path(__file__).resolve().parents[4]
COMPOSITION_DIR = REPO_ROOT / "video" / "compositions" / "quote_dim"
COMPOSITION_HTML = COMPOSITION_DIR / "compositions" / "quote_dim.html"
HYPERFRAMES = "hyperframes@0.7.72"

MIN_SEC = 4.0
MAX_SEC = 9.0  # 元件本身渲 9 秒，交件截到停留秒數
MAX_LINES = 3
# 96px 字從 x=240 起，留到 x≈1680（右下碎片在 y>890，不會碰到字）：1440px ≈ 15 個全形字
MAX_LINE_UNITS = 15.0
BREAK_AFTER = "，、。；：！？,.;:!?）」』》"
NO_LINE_START = "，、。；：！？,.;:!?）」』》"
MAX_WHO_UNITS = 30.0

Runner = Callable[[list[str], Path], None]


def char_units(ch: str) -> float:
    return 1.0 if unicodedata.east_asian_width(ch) in ("W", "F") else 0.55


def text_width_units(text: str) -> float:
    return sum(char_units(ch) for ch in text)


def wrap_line(text: str) -> list[str]:
    """一行太長就斷：盡量斷在標點後面；標點不放行首（可以吊在行尾）。"""
    lines: list[str] = []
    rest = text
    while text_width_units(rest) > MAX_LINE_UNITS:
        units, cut, last_punct = 0.0, 0, 0
        for i, ch in enumerate(rest):
            if units + char_units(ch) > MAX_LINE_UNITS:
                break
            units += char_units(ch)
            cut = i + 1
            if ch in BREAK_AFTER:
                last_punct = i + 1
        if last_punct and text_width_units(rest[:last_punct]) >= MAX_LINE_UNITS / 2:
            cut = last_punct
        while cut < len(rest) and rest[cut] in NO_LINE_START:
            cut += 1  # 標點吊在行尾
        lines.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        lines.append(rest)
    return lines


def quote_lines(row: Mapping[str, Any]) -> list[str]:
    text = (row.get("onscreen_text") or "").strip()
    if not text:
        raise delivery.DeliveryError(f"{row['id']}: 「畫面上的字」是空的")
    lines = [part for raw in text.splitlines() if raw.strip() for part in wrap_line(raw.strip())]
    if len(lines) > MAX_LINES:
        raise delivery.DeliveryError(
            f"{row['id']}: 金句太長（排成 {len(lines)} 行，上限 {MAX_LINES} 行），"
            "請在 Bridge 縮短或拆成兩張"
        )
    return lines


def attribution(row: Mapping[str, Any]) -> str:
    who = (row.get("source") or "").strip()
    if "\n" in who:
        raise delivery.DeliveryError(f"{row['id']}: 出處只能一行：{who!r}")
    if who.lower().startswith(("http://", "https://")):
        raise delivery.DeliveryError(f"{row['id']}: 出處會印在畫面上，請寫人名／書名，不要放網址")
    if text_width_units(who) > MAX_WHO_UNITS:
        raise delivery.DeliveryError(f"{row['id']}: 出處「{who}」太長，請縮短")
    return who


def show_seconds(span_sec: float) -> float:
    return round(min(MAX_SEC, max(MIN_SEC, span_sec)), 3)


def card_variables(plan: Mapping[str, Any], row: Mapping[str, Any]) -> tuple[dict, float, float]:
    if row.get("category") != CATEGORY:
        raise delivery.DeliveryError(f"{row.get('id')}: 不是金句卡")
    start, span = delivery.overlay_window(plan, row)
    seconds = show_seconds(span)
    variables = {
        "lines": "\n".join(quote_lines(row)),
        "who": attribution(row),
        "show_sec": seconds,
    }
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
                "compositions/quote_dim.html",
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
        # 元件固定渲 9 秒；交件長度＝停留秒數。ProRes 每格都是關鍵格，copy 截斷是逐格精準的。
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
    parser = argparse.ArgumentParser(description="渲金句卡（第 8 類）")
    parser.add_argument("episode", type=Path, help="集數資料夾（含 visual_plan.json）")
    parser.add_argument("--row", help="只渲這一列")
    parser.add_argument("--force", action="store_true", help="收據對得上也重渲")
    args = parser.parse_args(argv)

    plan = vp.load_plan(vp.plan_path(args.episode))
    rows = approved_rows(plan)
    if args.row:
        rows = [row for row in rows if row["id"] == args.row]
        if not rows:
            print(f"找不到核准的金句卡：{args.row}", file=sys.stderr)
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
        first = receipt["variables"]["lines"].split("\n")[0]
        print(
            f"{label} {row['id']}：「{first}…」"
            f" {receipt['start_sec']:.2f}s 起 {receipt['duration_sec']:.2f}s"
        )
    print(f"金句卡：{len(rows) - failures}/{len(rows)} 張完成")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
