"""Vault 路徑規則：集中 enforce CLAUDE.md vault 寫入限制。"""

from pathlib import PurePosixPath


class VaultRuleViolation(Exception):
    """路徑違反 vault 規則時拋出。"""


# Nami 可寫入的路徑前綴
_NAMI_WRITE_WHITELIST = ("Nami/Notes/",)

# Nami 可讀取的路徑前綴
_NAMI_READ_WHITELIST = (
    "Nami/Notes/",
    "Projects/",
    "TaskNotes/Tasks/",
)


def _normalize(relative_path: str) -> str:
    """正規化路徑並驗證無 traversal。回傳 POSIX 格式字串。"""
    if not relative_path or relative_path.strip() == "":
        raise VaultRuleViolation("路徑不得為空")

    # 拒絕絕對路徑
    p = PurePosixPath(relative_path)
    if p.is_absolute():
        raise VaultRuleViolation(f"路徑必須是 vault-relative（不能以 / 開頭）：{relative_path}")

    # 正規化後偵測 traversal（e.g. Nami/Notes/../KB/Raw/foo.md）
    resolved = str(PurePosixPath(*p.parts))  # collapse . but not ..
    parts = PurePosixPath(relative_path).parts
    if ".." in parts:
        raise VaultRuleViolation(f"路徑含有 '..'（path traversal）：{relative_path}")

    return resolved


def _is_under_prefix(normalized: str, prefix: str) -> bool:
    """檢查 normalized 是否在 prefix 底下（含 prefix 本身）。
    prefix 固定含結尾 /，normalized 可能沒有（PurePosixPath 會 strip）。
    例：normalized="Nami/Notes", prefix="Nami/Notes/" → True
        normalized="Nami/Notes/a.md", prefix="Nami/Notes/" → True
        normalized="Nami/NotesExtra/a.md", prefix="Nami/Notes/" → False
    """
    prefix_stripped = prefix.rstrip("/")
    return normalized == prefix_stripped or normalized.startswith(prefix_stripped + "/")


def assert_nami_can_write(relative_path: str) -> None:
    """Nami 寫入前驗證路徑合法。違規 raise VaultRuleViolation。"""
    normalized = _normalize(relative_path)
    for prefix in _NAMI_WRITE_WHITELIST:
        if _is_under_prefix(normalized, prefix):
            return
    allowed = ", ".join(_NAMI_WRITE_WHITELIST)
    raise VaultRuleViolation(f"Nami 不可寫入此路徑：{relative_path}。允許的前綴：{allowed}")


def assert_nami_can_read(relative_path: str) -> None:
    """Nami 讀取前驗證路徑合法。違規 raise VaultRuleViolation。"""
    normalized = _normalize(relative_path)
    for prefix in _NAMI_READ_WHITELIST:
        if _is_under_prefix(normalized, prefix):
            return
    allowed = ", ".join(_NAMI_READ_WHITELIST)
    raise VaultRuleViolation(f"Nami 不可讀取此路徑：{relative_path}。允許的前綴：{allowed}")
