from __future__ import annotations

import os
import subprocess

from shared.quiet_subprocess import quiet_kwargs


def test_windows_suppresses_the_console_window() -> None:
    """每次 render 都彈一個 cmd 視窗並搶焦點，等於整台機器不能用。"""
    kwargs = quiet_kwargs()
    if os.name != "nt":
        assert kwargs == {}
        return
    assert kwargs == {"creationflags": subprocess.CREATE_NO_WINDOW}


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
