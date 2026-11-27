"""第 10 類「自家舊片導流」、第 13 類「梗圖」：角落小卡 inset，渲成透明 overlay 素材。

設計（修修 2026-11-26 選 B：跟現行元件同一套設計語言）見
`video/compositions/inset_card/compositions/inset_card.html`：沿用書封的細橘框，
做成上方角落的卡；舊片導流多一塊「延伸觀看」字塊，梗圖不加。
修修 2026-11-27：卡放大到圖寬 720（手機上看得清縮圖的字），臉輕輕讓到另一側
（畫面搬移 nudge：1.3 倍、臉移到 40%／60%，幅度比書封小）。

圖從哪裡來：DP 找到圖、**搬進這一集的資料夾** `<episode>/assets/inset/`，寫規格檔
`<episode>/visual_sources/<row_id>.json`：

    {"image": "assets/inset/naval-video.jpg",
     "source": "https://youtu.be/btxEXuD-wu0", "note": "《納瓦爾寶典》獨自升級那集"}

- 舊片導流：`image` 用修修頻道那支影片的縮圖（`https://i.ytimg.com/vi/<id>/maxresdefault.jpg`）。
- 選填 `side`：`right`（預設）或 `left`——圖要放在臉的哪一側。

時間：從這一列蓋住的第一句字幕開始，停留＝那幾句的總長，夾在 3–4 秒之間
（臉讓開、退回各 0.5 秒，停太短畫面一直在動；2026-11-27 下限從 2 秒改 3 秒）。

用法：

    python -m agents.brook.youtube_video.visuals.inset "G:/Footages/<episode>" --check
    python -m agents.brook.youtube_video.visuals.inset "G:/Footages/<episode>" --row <id> --force
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from PIL import Image

from agents.brook.youtube_video import visual_plan as vp
from agents.brook.youtube_video.visuals import delivery
from shared.quiet_subprocess import quiet_kwargs

# 分類 → 框左上的字塊（空＝不加）
TAGS = {"old_video": "延伸觀看", "meme": ""}
COMPONENT = "inset_card"
REPO_ROOT = Path(__file__).resolve().parents[4]
COMPOSITION_DIR = REPO_ROOT / "video" / "compositions" / "inset_card"
COMPOSITION_HTML = COMPOSITION_DIR / "compositions" / "inset_card.html"
HYPERFRAMES = "hyperframes@0.7.72"
SOURCES_DIR = "visual_sources"
INSET_DIR = PurePosixPath("assets/inset")

MIN_SEC = 3.0
MAX_SEC = 4.0
SIDES = ("right", "left")
MIN_PIXELS = 320  # 圖的短邊至少這麼多，放大到小卡才不糊
MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}

Runner = Callable[[list[str], Path], None]


def show_seconds(span_sec: float) -> float:
    return round(min(MAX_SEC, max(MIN_SEC, span_sec)), 3)


def spec_path(episode_dir: Path, row_id: str) -> Path:
    return Path(episode_dir) / SOURCES_DIR / f"{row_id}.json"


def load_spec(episode_dir: Path, row_id: str) -> Mapping[str, Any]:
    path = spec_path(episode_dir, row_id)
    if not path.is_file():
        raise delivery.DeliveryError(
            f"{row_id}: 還沒有規格檔 {path}（DP 要先寫，格式見 skill youtube-inset）"
        )
    spec = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        raise delivery.DeliveryError(f"{row_id}: 規格檔要是一個物件（image、source……）")
    return spec


def image_file(episode_dir: Path, row_id: str, name: object) -> Path:
    """規格檔的 image → 集數資料夾 assets/inset/ 底下的實際檔案。"""
    hint = f"找到的圖要先搬進這一集的資料夾 {Path(episode_dir) / INSET_DIR}"
    if not isinstance(name, str) or not name.strip():
        raise delivery.DeliveryError(f"{row_id}: 規格檔要寫 image")
    rel = PurePosixPath(name.replace("\\", "/"))
    if rel.is_absolute() or ":" in name or ".." in rel.parts or rel.parent != INSET_DIR:
        raise delivery.DeliveryError(f"{row_id}: {name} 不在 {INSET_DIR}/ 裡——{hint}")
    if rel.suffix.lower() not in MIME:
        raise delivery.DeliveryError(f"{row_id}: {name} 要是圖檔（{'、'.join(MIME)}）")
    path = Path(episode_dir) / rel
    if not path.is_file():
        raise delivery.DeliveryError(f"{row_id}: 找不到 {path}——{hint}")
    return path


def image_ratio(row_id: str, data: bytes) -> float:
    try:
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
    except OSError:
        raise delivery.DeliveryError(f"{row_id}: 圖檔打不開") from None
    if min(width, height) < MIN_PIXELS:
        raise delivery.DeliveryError(
            f"{row_id}: 圖太小（{width}×{height}），短邊至少要 {MIN_PIXELS}，不然放上去會糊"
        )
    return round(width / height, 4)


def card_variables(
    episode_dir: Path, plan: Mapping[str, Any], row: Mapping[str, Any]
) -> tuple[dict, float, float, Mapping[str, Any]]:
    category = row.get("category")
    if category not in TAGS:
        raise delivery.DeliveryError(f"{row.get('id')}: 不是舊片導流或梗圖")
    if row.get("layout") == "full":
        raise delivery.DeliveryError(f"{row['id']}: 這一類是角落小卡，請在 Bridge 改成疊加")
    start, span = delivery.overlay_window(plan, row)
    seconds = show_seconds(span)
    spec = load_spec(episode_dir, row["id"])
    side = spec.get("side", "right")
    if side not in SIDES:
        raise delivery.DeliveryError(f"{row['id']}: side 只能是 right 或 left")
    path = image_file(episode_dir, row["id"], spec.get("image"))
    data = path.read_bytes()
    variables = {
        # 圖嵌進變數：收據的雜湊跟著圖檔內容走，換圖就重渲
        "image": f"data:{MIME[path.suffix.lower()]};base64,{base64.b64encode(data).decode()}",
        "ratio": image_ratio(row["id"], data),
        "side": side,
        "tag": TAGS[category],
        "show_sec": seconds,
    }
    return variables, start, seconds, spec


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
                "npx", "--yes", HYPERFRAMES, "render", ".",
                "-c", "compositions/inset_card.html",
                "-o", str(raw), "--format", "mov", "--fps", delivery.FPS,
                "-q", "standard", "--quiet", "--no-browser-gpu",
                "--variables-file", str(vars_file),
            ],
            COMPOSITION_DIR,
        )  # fmt: skip
        # 元件固定渲 8 秒；交件長度＝停留秒數（ProRes 每格關鍵格，copy 截斷逐格精準）
        runner(
            ["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-t", f"{seconds:.3f}",
             "-c", "copy", str(paths.media)],
            COMPOSITION_DIR,
        )  # fmt: skip
    finally:
        raw.unlink(missing_ok=True)
        vars_file.unlink(missing_ok=True)


def receipt_variables(variables: Mapping[str, Any]) -> dict[str, Any]:
    """收據不存整張圖（太大）；圖的內容已經算進 inputs_sha。"""
    slim = dict(variables)
    slim["image"] = f"<{len(variables['image'])} chars data URI>"
    return slim


def deliver_row(
    episode_dir: Path,
    plan: Mapping[str, Any],
    row: Mapping[str, Any],
    *,
    force: bool = False,
    runner: Runner = _hyperframes_render,
) -> tuple[dict[str, Any], bool]:
    """渲一列並寫收據。回傳（收據, 這次有沒有真的渲）。"""
    variables, start, seconds, spec = card_variables(episode_dir, plan, row)
    component_sha = delivery.file_sha(COMPOSITION_HTML)
    sha = delivery.inputs_sha(
        category=row["category"],
        component=COMPONENT,
        component_sha=component_sha,
        variables=variables,
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
        extra={
            "image_file": str(spec["image"]),
            "source": str(spec.get("source") or ""),
            "side": variables["side"],  # 鋪時間軸時臉讓到另一側
        },
    )
    return receipt, True


def rows_for(plan: Mapping[str, Any], include_pending: bool = False) -> list[Mapping[str, Any]]:
    wanted = {"approved", None} if include_pending else {"approved"}
    return [
        row
        for row in plan.get("rows", [])
        if row.get("category") in TAGS and row.get("status") in wanted
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="舊片導流／梗圖小卡（第 10、13 類）")
    parser.add_argument("episode", type=Path, help="集數資料夾（含 visual_plan.json）")
    parser.add_argument("--row", help="只做這一列")
    parser.add_argument("--check", action="store_true", help="只檢查規格檔，不渲")
    parser.add_argument("--force", action="store_true", help="收據對得上也重渲")
    parser.add_argument("--include-pending", action="store_true", help="待審的列也做（流程測試）")
    args = parser.parse_args(argv)

    plan = vp.load_plan(vp.plan_path(args.episode))
    rows = rows_for(plan, args.include_pending)
    if args.row:
        rows = [row for row in rows if row["id"] == args.row]
        if not rows:
            print(f"找不到這一列（或還沒核准）：{args.row}", file=sys.stderr)
            return 2
    failures = 0
    for row in rows:
        try:
            if args.check:
                variables, start, seconds, spec = card_variables(args.episode, plan, row)
                print(
                    f"可以交件 {row['id']}（{row['category']}）：{start:.2f}s 起 {seconds:.2f}s，"
                    f"{spec['image']}，放{'右' if variables['side'] == 'right' else '左'}側"
                )
                continue
            receipt, rendered = deliver_row(args.episode, plan, row, force=args.force)
        except delivery.DeliveryError as exc:
            failures += 1
            print(f"✗ {exc}", file=sys.stderr)
            continue
        label = "渲好" if rendered else "沿用"
        print(
            f"{label} {row['id']}（{row['category']}）"
            f" {receipt['start_sec']:.2f}s 起 {receipt['duration_sec']:.2f}s"
        )
    print(f"舊片導流／梗圖：{len(rows) - failures}/{len(rows)} 列完成")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
