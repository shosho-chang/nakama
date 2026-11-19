#!/usr/bin/env python3
"""render_youtube_cover.py — 主頻道 YouTube 影片封面出圖（桌機端）。

    python scripts/render_youtube_cover.py --episode-slug 20261027-wealth-ladder

讀 vault `Attachments/packaging/<slug>/youtube_cover.json` 的配方，用 headless Chromium 以
file:// 載入 Bridge 同一份版型（thousand_sunny/static/shosho/youtube-cover.{css,js}）截圖，
寫出 `cover.png`（1280×720）與 `cover-320.png`（YouTube 格線大小），回填配方檔的
`rendered_at`／`rendered_png`／`render_error`。

VPS 不出圖（ADR-054 D11）：Bridge 只存配方，本腳本由 `scripts/render_watcher.py` 在桌機呼叫，
也可以手動跑。

`rendered_at` 記的是**開始出圖的時間**：出圖途中 Bridge 又存了一次配方（requested_at 變新），
下一輪 watcher 會再出一次，不會被這次的回填蓋掉。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from shared.schemas.youtube_packaging import YouTubeCoverFileV1  # noqa: E402

STATIC = _REPO / "thousand_sunny" / "static" / "shosho"
COVER_FILE = "youtube_cover.json"
DEFAULT_CHROME = Path(
    os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright\chromium-1223\chrome-win64\chrome.exe")
)
LOGO = Path("E:/data/podcast thumbnail props/channel_logo_white.png")


def chrome_path() -> Path:
    env = os.environ.get("NAKAMA_CHROME", "").strip()
    return Path(env) if env else DEFAULT_CHROME


def file_uri(p: Path) -> str:
    return "file:///" + quote(str(p.resolve()).replace("\\", "/"), safe="/:")


def build_html(recipe: dict, vault: Path) -> str:
    vault_uri = file_uri(vault).rstrip("/") + "/"
    logo = file_uri(LOGO) if LOGO.is_file() else ""
    return f"""<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="{file_uri(STATIC / "youtube-cover.css")}">
<style>html,body{{margin:0;width:1280px;height:720px;overflow:hidden;background:#000}}</style>
</head><body><div id="c"></div>
<script src="{file_uri(STATIC / "youtube-cover.js")}"></script>
<script>
const VAULT = {json.dumps(vault_uri)};
const cover = YouTubeCover.mount(document.getElementById("c"), {{
  assetUrl: p => VAULT + p.split("/").map(encodeURIComponent).join("/"),
  logoUrl: {json.dumps(logo)} }});
cover.draw({json.dumps(recipe, ensure_ascii=False)});
cover.ready().then(() => {{ document.title = "ready"; }});
</script></body></html>"""


def _atomic_write(path: Path, data: dict) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def render_cover(slug: str, vault: Path, *, chrome: Path | None = None, run=subprocess.run) -> dict:
    """出圖並回填配方檔。回傳 {"png", "small"}；失敗時回填 render_error 後 raise。"""
    ep = vault / "Attachments" / "packaging" / slug
    path = ep / COVER_FILE
    raw = json.loads(path.read_text(encoding="utf-8"))
    cover = YouTubeCoverFileV1.model_validate(raw)
    started = datetime.now(timezone.utc)
    out = ep / "cover.png"
    try:
        with tempfile.TemporaryDirectory() as td:
            html = Path(td) / "cover.html"
            html.write_text(
                build_html(cover.recipe.model_dump(mode="json"), vault), encoding="utf-8"
            )
            run(
                [
                    str(chrome or chrome_path()),
                    "--headless=new",
                    "--disable-gpu",
                    "--hide-scrollbars",
                    "--allow-file-access-from-files",
                    "--force-device-scale-factor=1",
                    "--window-size=1280,720",
                    "--virtual-time-budget=8000",
                    f"--screenshot={out}",
                    file_uri(html),
                ],
                check=True,
                capture_output=True,
                timeout=120,
            )
        from PIL import Image

        im = Image.open(out).convert("RGB")
        if im.size != (1280, 720):
            raise RuntimeError(f"出圖尺寸不對：{im.size}")
        im.save(out)
        small = ep / "cover-320.png"
        im.resize((320, 180), Image.LANCZOS).save(small)
    except Exception as exc:
        latest = json.loads(path.read_text(encoding="utf-8"))
        latest["render_error"] = f"{type(exc).__name__}: {exc}"[:500]
        _atomic_write(path, latest)
        raise
    # 回填時重讀一次：出圖期間 Bridge 可能剛好又存了配方，只改出圖欄位，不動 recipe/requested_at
    latest = json.loads(path.read_text(encoding="utf-8"))
    latest.update(
        rendered_at=started.isoformat(),
        rendered_png=f"Attachments/packaging/{slug}/cover.png",
        render_error=None,
    )
    _atomic_write(path, latest)
    return {"png": str(out), "small": str(small)}


def main(argv: list[str] | None = None) -> int:
    from shared.config import get_vault_path

    ap = argparse.ArgumentParser(description="主頻道 YouTube 影片封面出圖")
    ap.add_argument("--episode-slug", required=True)
    ap.add_argument("--vault", type=Path, default=None)
    a = ap.parse_args(argv)
    vault = a.vault or get_vault_path()
    try:
        res = render_cover(a.episode_slug, vault)
    except Exception as exc:  # noqa: BLE001 — CLI 要把原因印出來
        print(f"出圖失敗：{exc}", file=sys.stderr)
        return 1
    print(f"OK → {res['png']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
