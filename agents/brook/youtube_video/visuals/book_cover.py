"""第 4 類「書封」：把規劃表上核准的列渲成透明 overlay 素材。

設計（修修 2026-11-15 定版）見
`video/compositions/book_cover_frame/compositions/book_cover_frame.html`：
書封正放、外圍一圈粗細有變化的細橘框，框從右上與左下兩角同時畫出來，書封在框畫到一半時拍上。
（repo 裡另有舊的 `video/compositions/book_cover/`，是 Podcast 長精華還在用的舊紙卡，不是這個。）

書從哪裡來：規劃表那一列的「出處」欄——
- 填書庫的書名（`data/books/<書名>/cover.*` 的資料夾名，`NAKAMA_BOOKS_DIR` 可改書庫位置），或
- 填一個圖檔的完整路徑（書庫沒有的書）。
「出處」空著時，改用「給 DP 的指示」裡第一個《書名》（Director 習慣這樣寫）。

位置：固定在畫面右側（臉之後由畫面搬移特效讓到左側 1/3）。
時間：從這一列蓋住的第一句字幕開始，停留＝那幾句的總長，夾在 2–6 秒之間（暫定，修修還沒定）。

用法：

    python -m agents.brook.youtube_video.visuals.book_cover "G:/Footages/<episode>"
    python -m agents.brook.youtube_video.visuals.book_cover "G:/Footages/<episode>" \\
        --row <id> --force
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from PIL import Image

from agents.brook.youtube_video import visual_plan as vp
from agents.brook.youtube_video.visuals import delivery
from shared.book_storage import books_root
from shared.quiet_subprocess import quiet_kwargs

CATEGORY = "book_cover"
COMPONENT = "book_cover_frame"
REPO_ROOT = Path(__file__).resolve().parents[4]
COMPOSITION_DIR = REPO_ROOT / "video" / "compositions" / "book_cover_frame"
COMPOSITION_HTML = COMPOSITION_DIR / "compositions" / "book_cover_frame.html"
HYPERFRAMES = "hyperframes@0.7.72"

MIN_SEC = 2.0
MAX_SEC = 6.0
SIDE = "right"
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}

Runner = Callable[[list[str], Path], None]


def show_seconds(span_sec: float) -> float:
    return round(min(MAX_SEC, max(MIN_SEC, span_sec)), 3)


def cover_source(row: Mapping[str, Any]) -> str:
    """「出處」欄；空著就取「給 DP 的指示」裡第一個《書名》。"""
    source = str(row.get("source") or "").strip()
    if source:
        return source
    match = re.search(r"《([^》]+)》", str(row.get("dp_instruction") or ""))
    return match.group(1) if match else ""


def find_cover(source: str, *, library: Path | None = None) -> Path:
    """「出處」欄 → 書封圖檔。先當圖檔路徑，再當書庫的書名。"""
    source = (source or "").strip().strip('"').strip("《》")
    if not source:
        raise delivery.DeliveryError(
            "不知道是哪本書：請在「出處」填書庫的書名或書封圖檔的路徑"
            "（或在「給 DP 的指示」寫《書名》）"
        )
    as_path = Path(source)
    if as_path.suffix.lower() in IMAGE_SUFFIXES:
        if as_path.is_file():
            return as_path
        raise delivery.DeliveryError(f"找不到書封圖檔：{source}")
    root = Path(library) if library is not None else books_root()
    book_dir = root / source
    covers = (
        sorted(p for p in book_dir.glob("cover.*") if p.suffix.lower() in IMAGE_SUFFIXES)
        if book_dir.is_dir()
        else []
    )
    if not covers:
        raise delivery.DeliveryError(
            f"書庫沒有《{source}》的書封（找過 {book_dir}\\cover.*）；請改填圖檔路徑"
        )
    return covers[0]


def cover_ratio(image_bytes: bytes) -> float:
    with Image.open(io.BytesIO(image_bytes)) as img:
        width, height = img.size
    if not width or not height:
        raise delivery.DeliveryError("書封圖檔讀不到尺寸")
    return round(width / height, 4)


def card_variables(
    plan: Mapping[str, Any], row: Mapping[str, Any], *, library: Path | None = None
) -> tuple[dict, float, float]:
    if row.get("category") != CATEGORY:
        raise delivery.DeliveryError(f"{row.get('id')}: 不是書封")
    if row.get("layout") == "full":
        raise delivery.DeliveryError(f"{row['id']}: 滿版書封還沒設計，請在 Bridge 改成疊加")
    start, span = delivery.overlay_window(plan, row)
    seconds = show_seconds(span)
    try:
        cover = find_cover(cover_source(row), library=library)
    except delivery.DeliveryError as exc:
        raise delivery.DeliveryError(f"{row['id']}: {exc}") from None
    data = cover.read_bytes()
    mime = MIME[cover.suffix.lower()]
    variables = {
        # 圖直接嵌進變數：元件資料夾不用放每本書的圖，收據的雜湊也跟著圖檔內容走
        "cover": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}",
        "cover_ratio": cover_ratio(data),
        "side": SIDE,
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
                "compositions/book_cover_frame.html",
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


def receipt_variables(variables: Mapping[str, Any]) -> dict[str, Any]:
    """收據不存整張圖（太大）；圖的內容已經算進 inputs_sha。"""
    slim = dict(variables)
    slim["cover"] = f"<{len(variables['cover'])} chars data URI>"
    return slim


def deliver_row(
    episode_dir: Path,
    plan: Mapping[str, Any],
    row: Mapping[str, Any],
    *,
    force: bool = False,
    runner: Runner = _hyperframes_render,
    library: Path | None = None,
) -> tuple[dict[str, Any], bool]:
    """渲一列並寫收據。回傳（收據, 這次有沒有真的渲）。"""
    variables, start, seconds = card_variables(plan, row, library=library)
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
        variables=receipt_variables(variables),
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
    parser = argparse.ArgumentParser(description="渲書封（第 4 類）")
    parser.add_argument("episode", type=Path, help="集數資料夾（含 visual_plan.json）")
    parser.add_argument("--row", help="只渲這一列")
    parser.add_argument("--force", action="store_true", help="收據對得上也重渲")
    args = parser.parse_args(argv)

    plan = vp.load_plan(vp.plan_path(args.episode))
    rows = approved_rows(plan)
    if args.row:
        rows = [row for row in rows if row["id"] == args.row]
        if not rows:
            print(f"找不到核准的書封：{args.row}", file=sys.stderr)
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
            f"{label} {row['id']}：{row.get('source')}"
            f" {receipt['start_sec']:.2f}s 起 {receipt['duration_sec']:.2f}s"
        )
    print(f"書封：{len(rows) - failures}/{len(rows)} 張完成")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
