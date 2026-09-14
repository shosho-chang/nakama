"""跑著的這個 process 載入的是哪一版程式碼，以及它是不是已經過期。

2026-09-14 同一個根因咬了兩次：#1257 把 packages.json 的「剛好 3 個封面」改成
「至多 3 個」，#1261 在發布頁加了複製鈕——兩個都合併進 main 了，修修看到的卻還是
舊行為。原因是 port 8000 上那個 uvicorn 是 9/11 啟動的，而 main 前進時沒有任何
東西會重啟它。三天是我們碰巧查出來的數字。

真正難受的不是「服務舊了」，是**看不出來它舊了**：畫面長得跟新版一模一樣，只有
行為不對。所以這裡回報的重點不是版本號，是 `stale` ——啟動當下載入的 commit 與
磁碟上現在的 commit 不同，就代表這個 process 跑的是舊碼，該重啟了。

不自動 pull、也不自動重啟：那會在剪片剪到一半把服務抽掉，風險比它解決的問題大。
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_GIT_TIMEOUT_SEC = 2.0


def _git(*args: str) -> str | None:
    """跑一個唯讀 git 指令；任何失敗都回 None，不讓它擋住頁面。

    部署方式不保證是 git checkout（tarball、容器層都可能），git 也可能不在
    PATH 上。這個資訊是輔助診斷用的，拿不到就不顯示，不是錯誤。
    """
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value or None


@dataclass(frozen=True)
class BootIdentity:
    """import 當下（≈ process 啟動）那一刻的程式碼身分。"""

    commit: str | None
    branch: str | None
    committed_at: str | None
    started_at: datetime


def _capture_boot_identity() -> BootIdentity:
    return BootIdentity(
        commit=_git("rev-parse", "HEAD"),
        branch=_git("rev-parse", "--abbrev-ref", "HEAD"),
        committed_at=_git("log", "-1", "--format=%cI"),
        started_at=datetime.now(UTC),
    )


# 只在 import 時算一次——這就是「這個 process 載入的版本」的定義。
BOOT = _capture_boot_identity()


def build_stamp(now: datetime | None = None) -> dict:
    """給 `/bridge/build` 的 payload。每次呼叫都重讀磁碟上的 HEAD。

    `stale` 是這整個模組存在的理由：它為 True 就表示有人已經把新程式碼放進
    工作目錄，但這個 process 還跑在舊的上面。
    """
    moment = now or datetime.now(UTC)
    current = _git("rev-parse", "HEAD")
    boot = BOOT.commit
    # 兩邊都拿不到 commit 時不能宣稱「一致」——那是不知道，不是沒問題。
    known = bool(boot and current)
    return {
        "commit": boot,
        "commit_short": boot[:7] if boot else None,
        "branch": BOOT.branch,
        "committed_at": BOOT.committed_at,
        "current_commit": current,
        "current_commit_short": current[:7] if current else None,
        "stale": bool(known and boot != current),
        "known": known,
        "started_at": BOOT.started_at.isoformat(),
        "uptime_seconds": max(0, int((moment - BOOT.started_at).total_seconds())),
    }
