"""把 episode 資料夾對到 Vault 的 interview 專案資料夾。

兩邊的命名規則不一樣，而且**推導不出來**：

- episode 資料夾用**上架日**：`20260901 蘇予昕`
- Vault interview 資料夾用**訪談日**：`2026-08-31-蘇予昕`

同一集差一天，別集可能差兩週。唯一穩定的共通欄位是來賓姓名，所以對應就靠它，
而且**只接受剛好一個**——對到零個或多個都停下來報清楚，不猜。
"""

from __future__ import annotations

import re
from pathlib import Path

from shared.config import get_vault_path

_EPISODE_DATE_PREFIX = re.compile(r"^\d{8}[\s_-]+")

INTERVIEWS_SUBPATH = ("AgentOutputs", "interviews")


class VaultInterviewError(RuntimeError):
    """對不到 Vault 的 interview 資料夾。"""


def guest_name(episode_id: str) -> str:
    """`20260901 蘇予昕` → `蘇予昕`。沒有日期前綴時整個名字就是來賓名。"""
    name = _EPISODE_DATE_PREFIX.sub("", episode_id.strip()).strip()
    # 只剩數字＝那是日期，不是人名。硬對下去會在 Vault 裡找一個叫「20260901」的
    # 來賓，然後回報「找不到」——錯誤訊息指向錯的地方。
    if not name or name.isdigit():
        raise VaultInterviewError(f"episode 資料夾名稱看不出來賓：{episode_id!r}")
    return name


def interviews_root(vault_path: Path | None = None) -> Path:
    root = (vault_path or get_vault_path()).joinpath(*INTERVIEWS_SUBPATH)
    if not root.is_dir():
        raise VaultInterviewError(f"Vault 的 interviews 目錄不存在：{root}")
    return root


def interview_dir(episode_id: str, vault_path: Path | None = None) -> Path:
    """找出這一集的 interview 專案資料夾。找不到或找到多個都 fail loud。"""
    guest = guest_name(episode_id)
    root = interviews_root(vault_path)
    matches = [
        path for path in sorted(root.iterdir()) if path.is_dir() and path.name.endswith(f"-{guest}")
    ]
    if not matches:
        raise VaultInterviewError(
            f"在 {root} 找不到來賓「{guest}」的 interview 資料夾"
            f"（episode={episode_id}）。這個資料夾是採集階段建的，"
            "不由本流程自行新建。"
        )
    if len(matches) > 1:
        names = [path.name for path in matches]
        raise VaultInterviewError(f"來賓「{guest}」對到多個 interview 資料夾：{names}")
    return matches[0]


def next_numbered_name(directory: Path, slug: str) -> str:
    """接續資料夾內既有的 `01-`…`06-` 編號，回傳 `07-<slug>.md` 這樣的檔名。

    同一個 slug 已經有檔案時沿用它的編號——重跑要覆蓋自己那一份，不是每跑一次
    就多疊一個號碼。
    """
    existing = sorted(path for path in directory.glob("[0-9][0-9]-*.md"))
    for path in existing:
        if path.stem[3:] == slug:
            return path.name
    used = [int(path.name[:2]) for path in existing]
    return f"{max(used, default=0) + 1:02d}-{slug}.md"
