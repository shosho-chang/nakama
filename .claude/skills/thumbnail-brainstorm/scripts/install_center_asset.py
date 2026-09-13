"""把 Envato 授權原檔裁成中央卡尺寸，安裝到 vault ＋ 該集 packaging。

N2 的中央卡是 678×455（1.4901），CSS `object-fit: cover`——素材比例不合就從短邊硬裁，
所以**先裁到卡片比例**再縮，進畫面的才是你挑的那一塊。Step 4.8 的血淚：一張 1080×1920
的直式素材只有 38% 進得了畫面，棲架與飼料碗全被切在框外。輸出 2× = 1356×910，
與既有 `center-<cut>-r<n>.png` 同規格。

這支走的是 **agent 路徑**（Step 4 自己配封面）。gate 路徑請用 `fetch_licensed_center.py`
——那支要 `approval.json` 裡已經有 `render_recipe` 才動得了。

    python install_center_asset.py <授權原檔> --episode-slug 20260901-suyuxin \
        --episode-dir "G:/Footages/20260901 蘇予昕" --cut-id punch-L02 --rank 3
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from PIL import Image

CARD_W, CARD_H = 1356, 910
TARGET = CARD_W / CARD_H
MIN_LONG_EDGE = 1280


def install(
    src: Path,
    *,
    episode_slug: str,
    episode_dir: Path,
    cut_id: str,
    rank: int,
    vault_root: Path,
    anchor: str = "center",
) -> Path:
    with Image.open(src) as image:
        image = image.convert("RGB")
        width, height = image.size
        if width <= height:
            raise SystemExit(f"{src.name} 是 {width}x{height} 直式——中央卡必須橫式")
        if max(width, height) < MIN_LONG_EDGE:
            raise SystemExit(
                f"{src.name} 長邊只有 {max(width, height)}px，低於 {MIN_LONG_EDGE}"
                "——這看起來還是浮水印預覽，不是授權原檔"
            )
        ratio = width / height
        if ratio > TARGET:  # 太寬 → 裁寬
            new_w = round(height * TARGET)
            x0 = (
                0
                if anchor == "left"
                else (width - new_w if anchor == "right" else (width - new_w) // 2)
            )
            box = (x0, 0, x0 + new_w, height)
        else:  # 太高 → 裁高
            new_h = round(width / TARGET)
            y0 = 0 if anchor == "top" else (height - new_h) // 2
            box = (0, y0, width, y0 + new_h)
        card = image.crop(box).resize((CARD_W, CARD_H), Image.LANCZOS)

    name = f"center-{cut_id}-r{rank}.png"
    vault_dir = vault_root / "Attachments" / "packaging" / episode_slug
    vault_dir.mkdir(parents=True, exist_ok=True)
    vault_png = vault_dir / name
    card.save(vault_png)

    working = episode_dir / "packaging"
    working.mkdir(parents=True, exist_ok=True)
    shutil.copy2(vault_png, working / name)

    print(f"{src.name}  {width}x{height} ({ratio:.3f}) → crop {box} → {CARD_W}x{CARD_H}")
    print(f"  → {vault_png}")
    print(f"  → {working / name}")
    return vault_png


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="授權原檔 → 中央卡（裁到卡片比例後安裝）")
    ap.add_argument("source", help="Envato 下載的授權原檔")
    ap.add_argument("--episode-slug", required=True)
    ap.add_argument("--episode-dir", required=True)
    ap.add_argument("--cut-id", required=True)
    ap.add_argument("--rank", type=int, required=True)
    ap.add_argument(
        "--anchor",
        default="center",
        choices=("center", "left", "right", "top"),
        help="主體不在正中間時挪裁切窗；預設置中",
    )
    args = ap.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
    from shared.config import get_vault_path

    install(
        Path(args.source),
        episode_slug=args.episode_slug,
        episode_dir=Path(args.episode_dir),
        cut_id=args.cut_id,
        rank=args.rank,
        vault_root=get_vault_path(),
        anchor=args.anchor,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
