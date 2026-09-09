from __future__ import annotations

import os
import subprocess

from shared.quiet_subprocess import quiet_kwargs


def test_windows_suppresses_the_console_window() -> None:
    """每次 render 都彈一個 cmd 視窗並搶焦點，等於整台機器不能用。

    這條在 CI 上其實不會驗到任何東西——runner 是 Linux，`os.name != "nt"` 就 early
    return 了。2026-09-08 加上 SW_HIDE 的那個 commit 只改了 `quiet_subprocess.py`、
    沒改這裡，於是這條測試在 Windows 上紅了一整天，GitHub 卻一路顯示 SUCCESS。
    Windows 專屬的行為，CI 看不到——改這個檔案時要在 Windows 上跑過。
    """
    kwargs = quiet_kwargs()
    if os.name != "nt":
        assert kwargs == {}
        return
    assert kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW
    startupinfo = kwargs["startupinfo"]
    assert startupinfo.dwFlags & subprocess.STARTF_USESHOWWINDOW
    assert startupinfo.wShowWindow == subprocess.SW_HIDE
    assert set(kwargs) == {"creationflags", "startupinfo"}


def test_kwargs_are_accepted_by_subprocess() -> None:
    """參數必須真的能傳給 subprocess.run，不是只長得像。"""
    command = ["cmd", "/c", "echo", "ok"] if os.name == "nt" else ["echo", "ok"]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        **quiet_kwargs(),
    )
    assert completed.returncode == 0
    assert "ok" in completed.stdout
