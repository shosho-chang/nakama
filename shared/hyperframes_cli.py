"""HyperFrames CLI 的唯一呼叫方式：版本鎖死在 ``video/package.json``，永遠不問。

2026-11-06 修修存了新封面，Bridge 一直顯示「沒有人在做」。原因不在 watcher：
``npx hyperframes`` 沒寫版本，npx 每次都去查 registry 的最新版。hyperframes 當天
發了 0.9.19，本機快取沒有，npx 就停在「Need to install … Ok to proceed? (y)」
等人按鍵——watcher 是排程起的、有 console，npx 認定它是互動終端機，於是永遠等下去。
render_request 的 600 秒逾時只殺掉直接子行程，卡住的 npx 會變成孤兒（當天還找到
一支 2026-10-27 起、同樣沒有開瀏覽器就停住的 npx hyperframes render）。

兩件事一起修：
- ``hyperframes@<確切版本>``：版本只從 ``video/package.json`` 讀，那也是封面
  sidecar 記錄 renderer 版本的同一個來源，記的就是實際跑的。上游發新版不會再悄悄
  換掉出圖引擎。
- ``--yes``：第一次用到這個版本時自動安裝，不會再停下來問。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

DEFAULT_VIDEO_DIR = Path(__file__).resolve().parents[1] / "video"

# 只收確切版本。`^0.8.0`、`latest` 這類範圍會讓 npx 回去查 registry，等於沒鎖。
_EXACT_VERSION = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?")


def pinned_version(video_dir: Path = DEFAULT_VIDEO_DIR) -> str:
    """``video/package.json`` 裡 hyperframes 的確切版本；不是確切版本就直接報錯。"""
    package = json.loads((video_dir / "package.json").read_text(encoding="utf-8"))
    version = (package.get("dependencies") or {}).get("hyperframes")
    if not isinstance(version, str) or not _EXACT_VERSION.fullmatch(version):
        raise RuntimeError(
            f"{video_dir / 'package.json'} 的 hyperframes 版本必須是確切版本"
            f"（例如 0.8.206），目前是 {version!r}"
        )
    return version


def npx_argv(*args: str, video_dir: Path = DEFAULT_VIDEO_DIR) -> list[str]:
    """``npx --yes hyperframes@<pinned> <args…>``。argv[0] 交給呼叫端解析成 npx.cmd。"""
    return ["npx", "--yes", f"hyperframes@{pinned_version(video_dir)}", *args]
