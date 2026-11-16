"""第 18 類「重點條列」：把規劃表上核准的條列列渲成透明 overlay 素材。

右側品牌灰側欄，條列一條一條列出。

設計（修修 2026-11-16 定版）見 `video/compositions/side_bullets/compositions/side_bullets.html`：
右側品牌冷灰側欄（左緣斜邊），左上橘色「重點整理」字牌，黑字配 60px 橘色編號（底部跟字切齊），
右下角從底邊探進品牌手冊第 24 頁的 3 塊橘色碎片。人由畫面搬移（reframe `left`）讓到左 1/3，
跟側欄同一秒開始。

字：「畫面上的字」（`onscreen_text`）一行一條，照講的順序。行首的「1.」「-」「・」會拿掉（編號由元件加）。
最多 6 條；每條最多排成 2 行（每行約 13 個全形字）；整份清單太高（會碰到右下碎片）就擋下。

時間：這一列蓋住的字幕＝整張停留的時間（從第一句開頭到最後一句結尾，3–60 秒）。
每一條在哪一句出現：
- 先在這段字幕裡依序找「第一」「第二」……（也認「第1」）——總結通常會這樣講——
  找齊了，第 N 條就在講「第 N」那句的開頭出現；
- 沒找齊，就把條列平均分配到這段字幕的句子開頭（指令會印出用了哪一種）。

用法：

    python -m agents.brook.youtube_video.visuals.side_bullets "G:/Footages/<episode>"
    python -m agents.brook.youtube_video.visuals.side_bullets "G:/Footages/<episode>" \\
        --row <id> --force
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from agents.brook.youtube_video import visual_plan as vp
from agents.brook.youtube_video.visuals import delivery
from shared.quiet_subprocess import quiet_kwargs

CATEGORY = "side_bullets"
COMPONENT = "side_bullets"
REPO_ROOT = Path(__file__).resolve().parents[4]
COMPOSITION_DIR = REPO_ROOT / "video" / "compositions" / "side_bullets"
COMPOSITION_HTML = COMPOSITION_DIR / "compositions" / "side_bullets.html"
HYPERFRAMES = "hyperframes@0.7.72"

TITLE = "重點整理"
MIN_SEC = 3.0
MAX_SEC = 60.0  # 元件本身渲 60 秒，交件截到停留秒數
FIRST_AT = 0.6  # 側欄 0.5 秒滑到位、標題 0.4 秒刷出；第一條最早在這之後
LAST_HOLD = 1.0  # 最後一條至少要在畫面上停這麼久才退場

MAX_ITEMS = 6
MAX_ITEM_LINES = 2
# 字從 x=1270 起（側欄 1210 ＋ 編號 38 ＋ 間距 22），右邊留 56px：594px ÷ 44px ≈ 13.5 個全形字
MAX_LINE_UNITS = 13.5
# 清單垂直置中；底部要停在右下碎片（最高點 y≈890）上面 → 整份高度上限 2×(870−540)
MAX_LIST_PX = 660
TITLE_BLOCK_PX = 88  # 標題字牌 74 ＋ 下方 14
GAP_PX = 34
ITEM_LINE_PX = 55  # 44px × 行高 1.25
NUM_PX = 60  # 60px 編號，單行的條目至少這麼高

LEADING_MARK = re.compile(r"^\s*(?:\d+\s*[.、)）]|[-•・*‧])\s*")
CN_DIGITS = "一二三四五六七八九"

Runner = Callable[[list[str], Path], None]


def char_units(ch: str) -> float:
    return 1.0 if unicodedata.east_asian_width(ch) in ("W", "F") else 0.55


def text_width_units(text: str) -> float:
    return sum(char_units(ch) for ch in text)


def item_lines(text: str) -> int:
    return max(1, math.ceil(text_width_units(text) / MAX_LINE_UNITS))


def list_height_px(items: Sequence[str]) -> int:
    rows = sum(max(NUM_PX, ITEM_LINE_PX * item_lines(t)) for t in items)
    return TITLE_BLOCK_PX + GAP_PX * len(items) + rows


def bullet_items(row: Mapping[str, Any]) -> list[str]:
    raw = (row.get("onscreen_text") or "").splitlines()
    items = [LEADING_MARK.sub("", line).strip() for line in raw]
    items = [t for t in items if t]
    if not items:
        raise delivery.DeliveryError(f"{row['id']}: 「畫面上的字」是空的（一行寫一條）")
    if len(items) > MAX_ITEMS:
        raise delivery.DeliveryError(
            f"{row['id']}: 條列有 {len(items)} 條，上限 {MAX_ITEMS} 條，請在 Bridge 刪減或拆成兩張"
        )
    for t in items:
        if item_lines(t) > MAX_ITEM_LINES:
            raise delivery.DeliveryError(
                f"{row['id']}: 「{t}」太長"
                f"（排成 {item_lines(t)} 行，上限 {MAX_ITEM_LINES} 行），請縮短"
            )
    if list_height_px(items) > MAX_LIST_PX:
        raise delivery.DeliveryError(
            f"{row['id']}: 條列整份太高，會碰到右下角碎片，請縮短幾條或刪一條"
        )
    return items


def _ordinal(n: int) -> re.Pattern[str]:
    """「第 n」：第一／第1／第１（全形）。後面不能再接數字（避免「第一」吃到「第十一」這類）。"""
    cn = CN_DIGITS[n - 1]
    full = chr(ord("０") + n)
    return re.compile(rf"第\s*(?:{cn}|{n}|{full})(?![0-9０-９一二三四五六七八九十])")


def ordinal_cues(cues: Sequence[Mapping[str, Any]], count: int) -> list[int] | None:
    """依序找講「第一」「第二」……的那幾句；找齊回傳句子的位置，否則 None。"""
    found: list[int] = []
    pos = 0
    for n in range(1, count + 1):
        pattern = _ordinal(n)
        hit = next((i for i in range(pos, len(cues)) if pattern.search(str(cues[i]["text"]))), None)
        if hit is None:
            return None
        found.append(hit)
        pos = hit + 1
    return found


def even_cues(cue_count: int, count: int) -> list[int]:
    return [math.floor(i * cue_count / count) for i in range(count)]


def item_times(
    cues: Sequence[Mapping[str, Any]], count: int, seconds: float
) -> tuple[list[float], str]:
    """每一條出現的秒數（從這張開始算）與用了哪一種對法（ordinal／even）。"""
    start = float(cues[0]["start"])
    picks = ordinal_cues(cues, count)
    method = "ordinal"
    if picks is None:
        method = "even"
        if len(cues) >= count:
            picks = even_cues(len(cues), count)
    if picks is not None:
        times = [max(FIRST_AT, float(cues[i]["start"]) - start) for i in picks]
    else:  # 句子比條列少：平均分配時間
        span = max(0.0, seconds - LAST_HOLD - FIRST_AT)
        times = [FIRST_AT + span * i / max(1, count - 1) for i in range(count)]
    times = [round(t, 3) for t in times]
    if times[-1] > seconds - LAST_HOLD:
        raise delivery.DeliveryError(
            f"最後一條在 {times[-1]:.1f} 秒才出現，整張只停 {seconds:.1f} 秒，請把字幕範圍往後拉"
        )
    return times, method


def card_variables(
    plan: Mapping[str, Any], row: Mapping[str, Any]
) -> tuple[dict, float, float, str]:
    if row.get("category") != CATEGORY:
        raise delivery.DeliveryError(f"{row.get('id')}: 不是重點條列")
    start, span = delivery.overlay_window(plan, row)
    seconds = round(span, 3)
    if seconds < MIN_SEC:
        raise delivery.DeliveryError(
            f"{row['id']}: 只蓋住 {seconds:.1f} 秒，條列至少要 {MIN_SEC:g} 秒"
        )
    if seconds > MAX_SEC:
        raise delivery.DeliveryError(
            f"{row['id']}: 蓋住 {seconds:.1f} 秒，上限 {MAX_SEC:g} 秒，請縮小字幕範圍或拆成兩張"
        )
    items = bullet_items(row)
    cues = vp.cue_range(plan, row["start_cue"], row["end_cue"])
    try:
        times, method = item_times(cues, len(items), seconds)
    except delivery.DeliveryError as exc:
        raise delivery.DeliveryError(f"{row['id']}: {exc}") from exc
    variables = {
        "title": TITLE,
        "items": "\n".join(items),
        "times": ",".join(f"{t:g}" for t in times),
        "show_sec": seconds,
    }
    return variables, start, seconds, method


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
                "compositions/side_bullets.html",
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
        # 元件固定渲 60 秒；交件長度＝停留秒數。ProRes 每格都是關鍵格，copy 截斷是逐格精準的。
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
    variables, start, seconds, method = card_variables(plan, row)
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
        extra={"item_timing": method},
    )
    return receipt, True


def approved_rows(plan: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [
        row
        for row in plan.get("rows", [])
        if row.get("category") == CATEGORY and row.get("status") == "approved"
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="渲重點條列（第 18 類）")
    parser.add_argument("episode", type=Path, help="集數資料夾（含 visual_plan.json）")
    parser.add_argument("--row", help="只渲這一列")
    parser.add_argument("--force", action="store_true", help="收據對得上也重渲")
    args = parser.parse_args(argv)

    plan = vp.load_plan(vp.plan_path(args.episode))
    rows = approved_rows(plan)
    if args.row:
        rows = [row for row in rows if row["id"] == args.row]
        if not rows:
            print(f"找不到核准的重點條列：{args.row}", file=sys.stderr)
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
        count = len(receipt["variables"]["items"].split("\n"))
        timing = "對準「第一…」" if receipt.get("item_timing") == "ordinal" else "平均分配"
        print(
            f"{label} {row['id']}：{count} 條（{timing}）"
            f" {receipt['start_sec']:.2f}s 起 {receipt['duration_sec']:.2f}s"
        )
    print(f"重點條列：{len(rows) - failures}/{len(rows)} 張完成")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
