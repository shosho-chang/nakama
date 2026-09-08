"""Windows 上不要為每個子行程彈一個主控台視窗。

`hyperframes` / `npx` / `node` / `ffmpeg` / `ffprobe` / Resolve CLI 每呼叫一次，
Windows 就開一個 cmd 視窗並**搶走鍵盤焦點**。一支長片要渲十幾張卡、每張還要
transcode，等於連續十幾次視窗閃跳——修修 2026-09-08 回報：「電腦會一直跑
Command 的視窗出來，那時候我完全沒辦法做其他的事情。」

這些呼叫一律已經用 `capture_output=True` 把 stdout/stderr 收走，主控台視窗
沒有任何用途，純粹是 Windows 的預設行為。

用法：

    from shared.quiet_subprocess import quiet_kwargs

    subprocess.run(argv, capture_output=True, text=True, **quiet_kwargs())

在非 Windows 平台回傳空 dict，行為完全不變。
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

__all__ = ["quiet_kwargs"]


def quiet_kwargs() -> dict[str, Any]:
    """回傳讓子行程不開主控台視窗的 `subprocess` 參數。

    `CREATE_NO_WINDOW` 只存在於 Windows；其他平台回空 dict。子行程再往下開的
    行程會繼承「沒有主控台」這件事，所以 `shell=True` 的 npx→node 鏈也一併安靜。
    """
    if os.name != "nt":
        return {}
    flag = getattr(subprocess, "CREATE_NO_WINDOW", None)
    if flag is None:  # pragma: no cover - 只在極舊的 Python 上發生
        return {}
    return {"creationflags": flag}
