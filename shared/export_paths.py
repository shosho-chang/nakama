"""發布成品的檔名——`publish_prep` 寫、其他人讀，規則只在這裡寫一次。

- 精華（長片／短片 winners）：`<episode>/highlights/exports/<cut_id>.mp4`
- 完整版（cut_id `full`）：`<episode>/final/<集數資料夾名>.mp4`，旁邊放同名的上傳字幕
  `<集數資料夾名>.srt`（Editorial Master `master.srt` 的逐位元組複本）與 podcast 音檔
  `<集數資料夾名>.mp3`。上 YouTube／podcast 平台的就是這幾個檔，檔名要是這一集的名字，
  不是 `full`（修修 2026-10-26）。集數資料夾名＝Resolve 專案名。
  `final/` 也是封面 `cover-full.png` 與 `title-full.txt` 的落點——上傳包只在一個資料夾
  （修修 2026-11-13：「為什麼會在 Highlights 裡面？應該在 Final 裡面才對」）。

舊集數的完整版在 `highlights/exports/<集數名>.mp4` 或更舊的 `full.mp4`，判斷「匯出過沒有」
時仍然認它們（`find_export_media`），不搬舊檔。

刻意不 import Resolve／publish_prep：Bridge 與派工盤點只是要問「檔在不在」。
"""

from __future__ import annotations

from pathlib import Path

EXPORTS_DIR = "highlights/exports"
#: 完整版上傳包（影片、上傳字幕、podcast 音檔、封面、標題）都在這裡。
FINAL_DIR = "final"
#: 完整版。它是 Editorial Master 那條 timeline 本身，不是挑出來的精華段落。
FULL_CUT_ID = "full"


def exports_dir(episode_dir: Path) -> Path:
    return Path(episode_dir) / EXPORTS_DIR


def output_dir(episode_dir: Path, cut_id: str) -> Path:
    """這一支的成品資料夾：完整版在 `final/`，精華在 `highlights/exports/`。"""
    if cut_id == FULL_CUT_ID:
        return Path(episode_dir) / FINAL_DIR
    return exports_dir(episode_dir)


def export_roots(episode_dir: Path) -> tuple[Path, Path]:
    """成品可以合法落腳的資料夾（Bridge 驗 receipt 路徑越界用）。"""
    return (Path(episode_dir) / FINAL_DIR, exports_dir(episode_dir))


def episode_dir_of_export(media_path: Path) -> Path:
    """由成品路徑反推集數資料夾：`final/<x>.mp4` 上一層、`highlights/exports/<x>.mp4` 上兩層。"""
    path = Path(media_path)
    if path.parent.name == FINAL_DIR:
        return path.parents[1]
    return path.parents[2]


def export_stem(episode_dir: Path, cut_id: str) -> str:
    """成品檔名（不含副檔名）：完整版用集數名，其他用 cut_id。"""
    return Path(episode_dir).name if cut_id == FULL_CUT_ID else cut_id


def export_media_path(episode_dir: Path, cut_id: str) -> Path:
    """這一支**現在**該 render 到哪裡。"""
    return output_dir(episode_dir, cut_id) / f"{export_stem(episode_dir, cut_id)}.mp4"


def export_audio_path(episode_dir: Path) -> Path:
    """完整版的 podcast 音檔。"""
    return output_dir(episode_dir, FULL_CUT_ID) / f"{Path(episode_dir).name}.mp3"


def export_subtitle_path(episode_dir: Path, cut_id: str) -> Path:
    """跟成品同名的 sidecar SRT。"""
    return output_dir(episode_dir, cut_id) / f"{export_stem(episode_dir, cut_id)}.srt"


def find_export_media(episode_dir: Path, cut_id: str) -> Path | None:
    """已經存在的成品：先找現行位置；完整版再退回舊集數的 `highlights/exports/` 兩種檔名。"""
    candidates = [export_media_path(episode_dir, cut_id)]
    if cut_id == FULL_CUT_ID:
        legacy = exports_dir(episode_dir)
        candidates += [legacy / f"{Path(episode_dir).name}.mp4", legacy / f"{FULL_CUT_ID}.mp4"]
    return next((path for path in candidates if path.is_file()), None)


def is_exported(episode_dir: Path, cut_id: str) -> bool:
    return find_export_media(episode_dir, cut_id) is not None
