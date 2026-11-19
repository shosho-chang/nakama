#!/usr/bin/env python3
"""init_youtube_packaging.py — 把一集主頻道 YouTube 影片的封面素材與起手配方放進 vault。

    python .claude/skills/book-thumbnail/scripts/init_youtube_packaging.py \
        --episode-slug 20261027-wealth-ladder --episode "20261027 財富階梯 AI Agent 版" \
        --feature "E:/nakama/data/books/財富階梯/cover.jpg" \
        --bg "E:/.../licensed_money_radiating.jpg" [--bg 另一張.jpg ...] \
        [--from-spec <本機原型 spec.json>] [--tracking <本機原型 title_tracking.json>]

做的事（vault `Attachments/packaging/<slug>/`）：
- `assets/feature.<ext>`：主圖（書籍介紹＝書封；主題介紹＝主題圖）
- `assets/bg-<名稱>.jpg`：背景。長邊大於 2560 會縮成 2560 的工作檔（授權原檔留在桌機，
  不讓幾 MB 的原檔同步到每台裝置）
- `youtube_cover.json`：起手配方（有 --from-spec 就照原型版面搬，`book` 改名 `feature`）。
  已存在時不覆蓋，除非 --force
- `title_tracking.json`：有 --tracking 就搬原型紀錄；否則由 Bridge 第一次開啟時依 packages.json 建
標題與大字候選仍由 title-brainstorm／emit_bigtext 寫 `packages.json`、`full/bigtext.json`。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

_REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_REPO))

from shared.schemas.youtube_packaging import TitleTrackingFileV1, YouTubeCoverFileV1  # noqa: E402

MAX_EDGE = 2560
DEFAULT_PERSON = "Attachments/cutouts/shosho/explaining/2.png"


def _copy_image(src: Path, dst: Path) -> None:
    from PIL import Image

    im = Image.open(src)
    if max(im.size) > MAX_EDGE:
        im = im.convert("RGB")
        im.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)
        im.save(dst.with_suffix(".jpg"), quality=92)
    else:
        shutil.copy2(src, dst)


def _rel(vault: Path, p: str) -> str:
    """原型 spec 的絕對路徑 → vault-relative（只接受 vault 內的去背照）。"""
    q = Path(p).resolve()
    try:
        return q.relative_to(vault.resolve()).as_posix()
    except ValueError:
        return ""


def main(argv: list[str] | None = None) -> int:
    from shared.config import get_vault_path

    ap = argparse.ArgumentParser()
    ap.add_argument("--episode-slug", required=True)
    ap.add_argument("--episode", required=True, help="footage 資料夾名")
    ap.add_argument("--feature", type=Path, required=True)
    ap.add_argument("--bg", type=Path, action="append", default=[])
    ap.add_argument("--from-spec", type=Path)
    ap.add_argument("--tracking", type=Path)
    ap.add_argument("--vault", type=Path)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)

    vault = a.vault or get_vault_path()
    ep = vault / "Attachments" / "packaging" / a.episode_slug
    assets = ep / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    base = f"Attachments/packaging/{a.episode_slug}/assets"

    fdst = assets / f"feature{a.feature.suffix.lower()}"
    _copy_image(a.feature, fdst)
    feature_rel = f"{base}/{fdst.name if fdst.exists() else fdst.with_suffix('.jpg').name}"
    bg_rels = []
    for bg in a.bg:
        dst = assets / f"bg-{bg.stem[:40]}{bg.suffix.lower()}"
        _copy_image(bg, dst)
        bg_rels.append(f"{base}/{dst.name if dst.exists() else dst.with_suffix('.jpg').name}")
    print(f"素材 → {assets}（{1 + len(bg_rels)} 張）")

    cover_path = ep / "youtube_cover.json"
    if cover_path.exists() and not a.force:
        print(f"{cover_path} 已存在，不覆蓋（要重建加 --force）")
    else:
        spec = json.loads(a.from_spec.read_text(encoding="utf-8")) if a.from_spec else {}
        feat = dict(spec.get("book") or spec.get("feature") or {})
        style = feat.get("frame_style") or ("none" if feat.get("frame") is False else "skew")
        person = dict(spec.get("person") or {})
        text = {k: v for k, v in (spec.get("text") or {}).items() if k != "en"}
        recipe = {
            "bg": {
                **{k: v for k, v in (spec.get("bg") or {}).items() if k != "src"},
                "src": bg_rels[0] if bg_rels else "",
            },
            "feature": {
                "src": feature_rel,
                "cx": feat.get("cx", 975),
                "cy": feat.get("cy", 372),
                "h": feat.get("h", 520),
                "tilt": feat.get("tilt", -12),
                "frame_style": style,
            },
            "person": {
                "src": _rel(vault, person.get("src", "")) or DEFAULT_PERSON,
                **{k: person[k] for k in ("cx", "top", "h", "glow", "flip") if k in person},
            },
            "text": {"x": 48, "y": 486, "size": 124, **text},
            "logo": {k: v for k, v in (spec.get("logo") or {}).items()},
            "bars": spec.get("bars", True),
        }
        recipe["person"].setdefault("cx", 420)
        recipe["person"].setdefault("top", 44)
        recipe["person"].setdefault("h", 860)
        doc = YouTubeCoverFileV1.model_validate(
            {
                "episode": a.episode,
                "recipe": recipe,
                "requested_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        cover_path.write_text(
            json.dumps(doc.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        print(f"配方 → {cover_path}（requested_at 已設，桌機 watcher 會出圖）")

    if a.tracking:
        tr = TitleTrackingFileV1.model_validate(json.loads(a.tracking.read_text(encoding="utf-8")))
        dst = ep / "title_tracking.json"
        if dst.exists() and not a.force:
            print(f"{dst} 已存在，不覆蓋")
        else:
            dst.write_text(
                json.dumps(tr.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"標題紀錄 → {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
