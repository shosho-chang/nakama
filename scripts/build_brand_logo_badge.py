"""把頻道 LOGO 動畫預合成成短片 opener 的 alpha 疊圖（ADR-067 短片線）。

輸出 ProRes 4444（alpha），與既有 `brand-badge-8s.mov` 同一套慣例：**位置烘焙在
檔案裡**，不靠 Resolve transform 的座標換算（那套座標語意是實測出來的，容易踩）。

## 來源

品牌資源的 deliverables 資料夾裡有 `*_alpha_prores4444.mov` 就用它——那是透明主檔，
旁邊的 `*_manifest.json` 還記了卡片外框與圓角，直接讀。

只有 `*_preview_*.mp4` 時（v11 那種），MP4 不支援透明：本 script 會 crop 掉展示底、
用 `geq` 依圓角半徑重建 alpha。**不能用 colorkey**——白卡與淺灰展示底只差約 15%
亮度，keying 會連線稿邊緣一起啃掉。

## 位置

預設 `--anchor seam-above`：卡片**底邊貼在上下分割的接縫上方**（修修 2026-08-30
二輪：「現在遮到我的頭太多了」——跨在接縫上會蓋住下半格主持人的頭頂，
下半格的臉幾乎從接縫就開始）。`--anchor seam-center` 是舊行為。

用法：
    python scripts/build_brand_logo_badge.py <episode> --source <alpha.mov> [--width 440]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

#: 卡片在 1080×1080 來源畫面裡的外框與圓角。有 manifest 就從 manifest 讀。
FALLBACK_CARD = {"bounds": [18, 114, 1062, 966], "corner_radius": 72}
CANVAS_W, CANVAS_H = 1080, 1920
SEAM_Y = CANVAS_H // 2
DEFAULT_WIDTH = 440
SEAM_GAP = 8  # 底邊與接縫之間留的縫，避免壓到下半格
#: 下半格主持人的耳機頭帶頂端離接縫只有約 13px（1080×1920 實測），
#: 所以 `--seam-offset` 往下移的空間很小；超過 ~40px 就會壓到頭。
SEAM_HEADROOM = 40


def _probe_pix_fmt(path: Path) -> str:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=pix_fmt",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return out


def _card_geometry(source: Path) -> tuple[list[int], int]:
    """卡片外框與圓角：優先讀同資料夾的 manifest，沒有就用量測過的常數。"""
    for candidate in source.parent.glob("*_manifest.json"):
        try:
            card = json.loads(candidate.read_text(encoding="utf-8"))["card"]
            return list(card["bounds"]), int(card["corner_radius"])
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
            continue
    return list(FALLBACK_CARD["bounds"]), int(FALLBACK_CARD["corner_radius"])


def _rounded_alpha(w: int, h: int, r: int) -> str:
    return (
        "if(lte(hypot("
        f"max(0\\,max({r}-X\\,X-({w}-1-{r})))\\,"
        f"max(0\\,max({r}-Y\\,Y-({h}-1-{r})))"
        f"),{r}),255,0)"
    )


def build(source: Path, out: Path, width: int, anchor: str, seam_offset: int = 0) -> Path:
    (x0, y0, x1, y1), src_radius = _card_geometry(source)
    crop_w, crop_h = x1 - x0, y1 - y0
    card_w = width
    card_h = round(card_w * crop_h / crop_w / 2) * 2
    pad_x = (CANVAS_W - card_w) // 2
    if anchor == "seam-above":
        pad_y = SEAM_Y - SEAM_GAP - card_h + seam_offset
    elif anchor == "seam-center":
        pad_y = SEAM_Y - card_h // 2
    else:  # pragma: no cover - argparse 已限制
        raise SystemExit(f"未知的 anchor：{anchor}")
    if pad_y < 0:
        raise SystemExit(f"--width {card_w} 太大：卡片高 {card_h}px 放不進接縫上方")

    steps = [f"crop={crop_w}:{crop_h}:{x0}:{y0}", f"scale={card_w}:{card_h}:flags=lanczos"]
    if "a" not in _probe_pix_fmt(source):
        # 展示底預覽（MP4 無 alpha）：依圓角半徑重建
        radius = round(src_radius * card_w / crop_w)
        mask = _rounded_alpha(card_w, card_h, radius)
        steps += ["format=rgba", f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='{mask}'"]
    steps += [f"pad={CANVAS_W}:{CANVAS_H}:{pad_x}:{pad_y}:color=black@0", "format=yuva444p10le"]

    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(source),
            "-vf",
            ",".join(steps),
            "-an",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "4444",
            "-pix_fmt",
            "yuva444p10le",
            str(out),
            "-y",
        ],
        check=True,
    )
    print(
        f"{out}  card {card_w}x{card_h} @({pad_x},{pad_y})–({pad_x + card_w},{pad_y + card_h})"
        f"  {out.stat().st_size:,} bytes"
    )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="頻道 LOGO 動畫 → 短片 opener alpha 疊圖")
    parser.add_argument("episode")
    parser.add_argument("--source", type=Path, required=True, help="LOGO 動畫（優先用 alpha 主檔）")
    parser.add_argument("--slug", default="brand-logo-opener")
    parser.add_argument(
        "--width",
        type=int,
        default=DEFAULT_WIDTH,
        help=f"卡片寬度（畫布 {CANVAS_W}px；預設 {DEFAULT_WIDTH}）",
    )
    parser.add_argument(
        "--seam-offset",
        type=int,
        default=0,
        help=f"往下移幾 px（seam-above 用）；下半格的頭只留約 {SEAM_HEADROOM}px 餘裕",
    )
    parser.add_argument(
        "--anchor",
        choices=("seam-above", "seam-center"),
        default="seam-above",
        help="seam-above＝底邊貼接縫上方（預設）；seam-center＝跨在接縫上",
    )
    args = parser.parse_args(argv)
    if not args.source.is_file():
        raise SystemExit(f"找不到 LOGO 動畫：{args.source}")
    if not 200 <= args.width <= CANVAS_W:
        raise SystemExit(f"--width {args.width} 不合理（200–{CANVAS_W}）")
    if abs(args.seam_offset) > SEAM_HEADROOM:
        raise SystemExit(
            f"--seam-offset {args.seam_offset} 超過 ±{SEAM_HEADROOM}px——"
            "再往下就壓到下半格主持人的頭了"
        )
    build(
        args.source,
        Path(args.episode) / "assets" / "broll" / f"{args.slug}.mov",
        args.width,
        args.anchor,
        args.seam_offset,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
