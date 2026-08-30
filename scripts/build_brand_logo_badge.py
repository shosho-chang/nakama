"""把頻道 LOGO 動畫預合成成短片 opener 的 alpha 疊圖（ADR-067 短片線）。

品牌資源裡的 LOGO 動畫是 **1080×1080、淺灰底、沒有 alpha** 的預覽檔，中間嵌一張
白色圓角卡。直接疊上去會出現一個灰方塊，所以要先把卡切出來、補回圓角透明。

三個步驟：

1. `crop` 掉灰底，只留白卡
2. `geq` 依圓角半徑重建 alpha——**不能用 colorkey**：白卡與灰底只差約 15% 亮度，
   keying 會連線稿邊緣一起啃掉
3. 縮到 badge 尺寸、pad 進 1080×1920 透明畫布，位置預設在**上下分割的接縫**上

輸出 ProRes 4444（alpha），與既有 `brand-badge-8s.mov` 同一套慣例：**位置烘焙在
檔案裡**，不靠 Resolve transform 的座標換算（那套座標語意是實測出來的，容易踩）。

落地後在 `<id>_broll.json` 加一筆 structural item 即可（不需要授權收據，這是自家
品牌資產）：

    {"kind": "badge", "slug": "brand-logo-opener", "t0": 0.0, "t1": 3.933}

用法：
    python scripts/build_brand_logo_badge.py <episode> [--source <mp4>] [--width 620]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

DEFAULT_SOURCE = Path(
    "E:/Company/02_品牌資源_BrandAssets/Logo/animation/"
    "podcast_rounded_card_white_full_fade_v11_preview_light.mp4"
)
#: 白卡在 1080×1080 來源畫面裡的外框與圓角（量測值；換版本要重量）。
CARD_CROP = (1044, 852, 18, 114)  # w, h, x, y
CARD_RADIUS = 70
CANVAS_W, CANVAS_H = 1080, 1920
DEFAULT_WIDTH = 620


def build(source: Path, out: Path, width: int) -> Path:
    crop_w, crop_h, crop_x, crop_y = CARD_CROP
    card_w = width
    card_h = round(card_w * crop_h / crop_w / 2) * 2
    radius = round(CARD_RADIUS * card_w / crop_w)
    pad_x = (CANVAS_W - card_w) // 2
    pad_y = CANVAS_H // 2 - card_h // 2  # 上下分割的接縫
    alpha = (
        "if(lte(hypot("
        f"max(0\\,max({radius}-X\\,X-({card_w}-1-{radius})))\\,"
        f"max(0\\,max({radius}-Y\\,Y-({card_h}-1-{radius})))"
        f"),{radius}),255,0)"
    )
    vf = (
        f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
        f"scale={card_w}:{card_h}:flags=lanczos,"
        "format=rgba,"
        f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='{alpha}',"
        f"pad={CANVAS_W}:{CANVAS_H}:{pad_x}:{pad_y}:color=black@0,"
        "format=yuva444p10le"
    )
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
            vf,
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
        f"{out}  card {card_w}x{card_h} r={radius} @({pad_x},{pad_y})  {out.stat().st_size:,} bytes"
    )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="頻道 LOGO 動畫 → 短片 opener alpha 疊圖")
    parser.add_argument("episode")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--slug", default="brand-logo-opener")
    parser.add_argument(
        "--width",
        type=int,
        default=DEFAULT_WIDTH,
        help=f"卡片寬度（畫布 {CANVAS_W}px；預設 {DEFAULT_WIDTH}）",
    )
    args = parser.parse_args(argv)
    if not args.source.is_file():
        raise SystemExit(f"找不到 LOGO 動畫：{args.source}")
    if not 200 <= args.width <= CANVAS_W:
        raise SystemExit(f"--width {args.width} 不合理（200–{CANVAS_W}）")
    build(args.source, Path(args.episode) / "assets" / "broll" / f"{args.slug}.mov", args.width)
    return 0


if __name__ == "__main__":
    sys.exit(main())
