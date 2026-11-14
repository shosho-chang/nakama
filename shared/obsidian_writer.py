"""Obsidian vault 寫入工具：產生含 frontmatter 的 Markdown 檔案。

所有寫入／追加／刪除都經過 ``_vault_target``（2026-11 測試審計補的共用防線）：

- 路徑必須留在 vault 裡面（擋 ``..``、絕對路徑）。
- ``Journals/`` 一律不准寫、不准刪（CLAUDE.md「Vault 寫入規則」）。
- ``delete_page`` 不再直接 ``unlink``，改移到 vault 的 ``.trash/``——Obsidian
  「已刪除檔案」看得到、救得回來；VPS（Linux）上沒有系統回收桶，這是唯一可逆的做法。
"""

from datetime import date, datetime
from pathlib import Path, PurePath

import yaml

from shared.config import get_vault_path

#: vault 第一層禁止 agent 寫入／刪除的資料夾（比對不分大小寫）。
PROTECTED_TOP_DIRS = frozenset({"journals"})

#: Obsidian 自己的 vault 內垃圾桶資料夾（設定「移到 Obsidian 垃圾桶」用的同一個）。
TRASH_DIR = ".trash"


class VaultWriteForbidden(PermissionError):
    """要寫入／刪除的路徑在 vault 外，或落在禁止寫入的資料夾。"""


def vault_path(*parts: str) -> Path:
    """組合 vault 內的相對路徑為絕對路徑。"""
    return get_vault_path().joinpath(*parts)


def _vault_target(relative_path: str | PurePath, *, action: str) -> Path:
    """把 vault 相對路徑轉成絕對路徑，並擋掉 vault 外與 ``Journals/``。"""
    root = get_vault_path().resolve()
    target = (root / relative_path).resolve()
    try:
        rel = target.relative_to(root)
    except ValueError:
        raise VaultWriteForbidden(f"拒絕{action}：{relative_path} 不在 vault 裡面") from None
    if not rel.parts:
        raise VaultWriteForbidden(f"拒絕{action}：路徑指到 vault 根目錄本身")
    if rel.parts[0].lower() in PROTECTED_TOP_DIRS:
        raise VaultWriteForbidden(f"拒絕{action}：{rel.as_posix()} 在 Journals/，agent 不准動")
    return target


def write_page(
    relative_path: str,
    frontmatter: dict,
    body: str,
    *,
    overwrite: bool = True,
) -> Path:
    """寫入一頁 Markdown 到 vault。

    Args:
        relative_path: 相對於 vault root 的路徑，例如 "KB/Wiki/Sources/xxx.md"
        frontmatter: YAML frontmatter dict
        body: Markdown 內容（不含 frontmatter）
        overwrite: 是否覆寫既有檔案

    Raises:
        VaultWriteForbidden: 路徑在 vault 外或在 ``Journals/``。
    """
    target = _vault_target(relative_path, action="寫入")
    if target.exists() and not overwrite:
        return target

    target.parent.mkdir(parents=True, exist_ok=True)

    # 確保 updated 欄位為今天
    frontmatter.setdefault("created", str(date.today()))
    frontmatter["updated"] = str(date.today())

    fm_str = yaml.dump(
        frontmatter,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=10**9,
    ).strip()

    content = f"---\n{fm_str}\n---\n\n{body}\n"
    target.write_text(content, encoding="utf-8")
    return target


def read_page(relative_path: str) -> str | None:
    """讀取 vault 內的 Markdown 檔案，回傳完整內容。不存在時回傳 None。"""
    target = get_vault_path() / relative_path
    if not target.exists():
        return None
    return target.read_text(encoding="utf-8")


def append_to_file(relative_path: str, text: str) -> None:
    """在 vault 內的檔案末尾追加文字（用於 log.md 等 append-only 檔案）。"""
    target = _vault_target(relative_path, action="追加")
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8") as f:
        f.write(text)


def _trash_destination(root: Path, rel: Path) -> Path:
    dest = root / TRASH_DIR / rel
    if not dest.exists():
        return dest
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = dest.with_name(f"{dest.stem} ({stamp}){dest.suffix}")
    n = 2
    while candidate.exists():
        candidate = dest.with_name(f"{dest.stem} ({stamp}-{n}){dest.suffix}")
        n += 1
    return candidate


def delete_page(relative_path: str) -> bool:
    """把 vault 內的檔案移到 vault 的 ``.trash/``（保留原本的資料夾結構）。

    檔案不存在時返回 False，成功移走返回 True。同名檔已在 ``.trash/`` 時加上時間戳，
    不覆蓋舊的。不會永久刪除任何東西。

    Raises:
        VaultWriteForbidden: 路徑在 vault 外或在 ``Journals/``。
    """
    target = _vault_target(relative_path, action="刪除")
    if not target.exists():
        return False
    root = get_vault_path().resolve()
    rel = target.relative_to(root)
    if rel.parts[0] == TRASH_DIR:
        return False  # 已經在垃圾桶裡，不再搬（也不永久刪）
    dest = _trash_destination(root, rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    target.replace(dest)
    return True


def list_files(relative_dir: str, suffix: str = ".md") -> list[Path]:
    """列出 vault 內某資料夾下所有指定副檔名的檔案。"""
    target = get_vault_path() / relative_dir
    if not target.exists():
        return []
    return sorted(target.glob(f"*{suffix}"))
