r"""跨集共用的 BGM／SFX 素材庫。

修修 2026-09-10 盤點出來的斷點：`assets/bgm`、`assets/sfx` 全部是 per-episode，
每一集都要**手抄**。20260901 蘇予昕 那集實際發生的：

- BGM 從 `E:\data\music\short-punch` 手動轉檔複製（庫是 mp3，工具只讀 wav）
- 那五個 SFX（ding／impact／pop／riser／swish）只存在於 20260723 謝伯讓 那一集，
  要從那裡複製過來

素材庫在 `E:\data`（`NAKAMA_ASSET_LIBRARY` 可覆寫）。解析順序永遠是
**集內優先、庫次之**：某一集想用不一樣的東西，把檔案放進 `assets/` 就贏過庫，
不需要改參數，也不會回頭去污染庫。

mp3 → wav 的轉檔在**集內 cache** 做，不寫回庫——庫是唯讀的來源，不是暫存區。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_AUDIO_SUFFIXES = (".wav", ".mp3", ".m4a", ".flac", ".aac", ".ogg")

# miner family → 音樂庫子資料夾。長片走 focus music（見 reference_music_library）。
BGM_FAMILY_DIRS: dict[str, str] = {
    "punch": "short-punch",
    "story": "short-story",
    "value": "short-value",
    "long": "focus music",
    "uplifting": "uplifting music",
}


class AssetLibraryError(RuntimeError):
    """素材找不到，或轉檔失敗。"""


def library_root() -> Path:
    return Path(os.environ.get("NAKAMA_ASSET_LIBRARY", r"E:\data"))


def _search_dirs(kind: str, family: str | None) -> list[Path]:
    root = library_root()
    if kind == "sfx":
        return [root / "sfx"]
    music = root / "music"
    dirs: list[Path] = []
    if family and family in BGM_FAMILY_DIRS:
        dirs.append(music / BGM_FAMILY_DIRS[family])
    # 指名的家族優先，但不排除其他家族：修修常常直接講曲名。
    dirs.extend(music / name for name in BGM_FAMILY_DIRS.values())
    dirs.append(music)
    seen: set[Path] = set()
    return [d for d in dirs if not (d in seen or seen.add(d))]


def find_in_library(name: str, kind: str, family: str | None = None) -> Path | None:
    """庫裡叫這個名字的檔（不分副檔名）。找不到回 None——呼叫端才知道怎麼報。

    庫裡的檔名帶著 Envato 的流水號尾巴（`slow-edges-mum-child-main-version-48501-01-45.mp3`），
    而修修講的是前面那段。所以完全相符優先，其次才是前綴相符。
    """
    stem = name.lower().removesuffix(".wav")
    prefix_hit: Path | None = None
    for directory in _search_dirs(kind, family):
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if not path.is_file() or path.suffix.lower() not in _AUDIO_SUFFIXES:
                continue
            candidate = path.stem.lower()
            if candidate == stem:
                return path
            if prefix_hit is None and candidate.startswith(stem):
                prefix_hit = path
    return prefix_hit


def _transcode_to_wav(src: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        return destination
    staging = destination.with_suffix(".partial.wav")
    result = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), str(staging)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not staging.is_file():
        staging.unlink(missing_ok=True)
        raise AssetLibraryError(f"轉檔失敗 {src.name} → wav：{result.stderr.strip()[:400]}")
    staging.replace(destination)
    return destination


def ensure_asset(
    episode_dir: Path,
    name: str,
    *,
    kind: str,
    family: str | None = None,
) -> Path:
    """回傳集內那份 `assets/<kind>/<name>.wav`，缺了就從庫取（必要時轉檔）。

    `kind` 是 `"bgm"` 或 `"sfx"`。集內已經有就直接用——**per-episode 覆寫永遠贏**。
    """
    local = episode_dir / "assets" / kind / f"{name}.wav"
    if local.is_file():
        return local
    source = find_in_library(name, kind, family)
    if source is None:
        root = library_root()
        searched = "、".join(str(d) for d in _search_dirs(kind, family) if d.is_dir())
        raise AssetLibraryError(
            f"找不到 {kind} 素材「{name}」：集內沒有 {local}，"
            f"素材庫（{root}）也沒有。已找過：{searched or '（庫的目錄都不存在）'}"
        )
    if source.suffix.lower() == ".wav":
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(source.read_bytes())
        return local
    return _transcode_to_wav(source, local)
