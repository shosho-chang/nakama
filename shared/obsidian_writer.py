"""Obsidian vault 寫入工具：產生含 frontmatter 的 Markdown 檔案。"""

from datetime import date
from pathlib import Path

import yaml

from shared.config import get_vault_path


def vault_path(*parts: str) -> Path:
    """組合 vault 內的相對路徑為絕對路徑。"""
    return get_vault_path().joinpath(*parts)


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
    """
    target = get_vault_path() / relative_path
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
    target = get_vault_path() / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8") as f:
        f.write(text)


def delete_page(relative_path: str) -> bool:
    """刪除 vault 內的檔案。檔案不存在時返回 False，成功刪除返回 True。"""
    target = get_vault_path() / relative_path
    if not target.exists():
        return False
    target.unlink()
    return True


def list_files(relative_dir: str, suffix: str = ".md") -> list[Path]:
    """列出 vault 內某資料夾下所有指定副檔名的檔案。"""
    target = get_vault_path() / relative_dir
    if not target.exists():
        return []
    return sorted(target.glob(f"*{suffix}"))
