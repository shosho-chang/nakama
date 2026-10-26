"""發布成品的檔名——`publish_prep` 寫、其他人讀，規則只在這裡寫一次。

- 精華（長片／短片 winners）：`<episode>/highlights/exports/<cut_id>.mp4`
- 完整版（cut_id `full`）：`<episode>/highlights/exports/<集數資料夾名>.mp4`，
  旁邊放同名的上傳字幕 `<集數資料夾名>.srt`（Editorial Master `master.srt` 的
  逐位元組複本）。上 YouTube 的就是這兩個檔，檔名要是這一集的名字，不是 `full`
  （修修 2026-10-26）。集數資料夾名＝Resolve 專案名。

舊集數的完整版是 `full.mp4`，判斷「匯出過沒有」時仍然認它（`find_export_media`）。

刻意不 import Resolve／publish_prep：Bridge 與派工盤點只是要問「檔在不在」。
"""

from __future__ import annotations

from pathlib import Path

EXPORTS_DIR = "highlights/exports"
#: 完整版。它是 Editorial Master 那條 timeline 本身，不是挑出來的精華段落。
FULL_CUT_ID = "full"


def exports_dir(episode_dir: Path) -> Path:
    return Path(episode_dir) / EXPORTS_DIR


def export_stem(episode_dir: Path, cut_id: str) -> str:
    """成品檔名（不含副檔名）：完整版用集數名，其他用 cut_id。"""
    return Path(episode_dir).name if cut_id == FULL_CUT_ID else cut_id


def export_media_path(episode_dir: Path, cut_id: str) -> Path:
    """這一支**現在**該 render 到哪裡。"""
    return exports_dir(episode_dir) / f"{export_stem(episode_dir, cut_id)}.mp4"


def export_subtitle_path(episode_dir: Path, cut_id: str) -> Path:
    """跟成品同名的 sidecar SRT。"""
    return exports_dir(episode_dir) / f"{export_stem(episode_dir, cut_id)}.srt"


def find_export_media(episode_dir: Path, cut_id: str) -> Path | None:
    """已經存在的成品：先找現行檔名，完整版再退回舊的 `full.mp4`。"""
    candidates = [export_media_path(episode_dir, cut_id)]
    if cut_id == FULL_CUT_ID:
        candidates.append(exports_dir(episode_dir) / f"{FULL_CUT_ID}.mp4")
    return next((path for path in candidates if path.is_file()), None)


def is_exported(episode_dir: Path, cut_id: str) -> bool:
    return find_export_media(episode_dir, cut_id) is not None
