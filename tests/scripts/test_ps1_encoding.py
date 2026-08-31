"""含非 ASCII 的 PowerShell 腳本必須有 UTF-8 BOM。

Windows PowerShell 5.1（修修機器上的預設）讀 `.ps1` **沒有 BOM 就當成 ANSI**
（這台是 cp950）。中文只在註解裡時亂碼掉還能跑；一旦中文出現在**字串**裡，
拆出來的位元組可能剛好是引號，直接把語法弄斷：

    The string is missing the terminator: ".

2026-08-31 實際發生：`install_thousand_sunny_task.ps1` 的
`Write-Output "看日誌： Get-Content ..."` 讓整支腳本 parse 失敗，修修跑不起來。
同一個檔案加上 BOM 之後 `[Parser]::ParseFile` 就過。

這條測試擋的是「下次又寫了一支帶中文字串、沒有 BOM 的 .ps1」。
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKIP_PARTS = {".git", "node_modules", ".venv", ".venv-v2", ".cache"}
BOM = b"\xef\xbb\xbf"


def _powershell_scripts() -> list[Path]:
    return [
        p
        for p in sorted(REPO.rglob("*.ps1"))
        if not SKIP_PARTS.intersection(p.relative_to(REPO).parts)
    ]


def test_non_ascii_powershell_scripts_have_utf8_bom():
    offenders = []
    for path in _powershell_scripts():
        raw = path.read_bytes()
        if raw.startswith(BOM):
            continue
        try:
            is_ascii = raw.decode("utf-8").isascii()
        except UnicodeDecodeError:
            is_ascii = False
        if not is_ascii:
            offenders.append(str(path.relative_to(REPO)))
    assert not offenders, (
        "這些 .ps1 含非 ASCII 但沒有 UTF-8 BOM，Windows PowerShell 5.1 會讀成 ANSI "
        f"並可能 parse 失敗：{offenders}"
    )


def test_audit_actually_sees_the_scripts():
    """rglob 若因為路徑變動掃不到東西，上面那條會空過——這裡釘住它有在看。"""
    assert len(_powershell_scripts()) >= 3
