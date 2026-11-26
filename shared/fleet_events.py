"""呼叫 freedom-fleet 的 ``python -m fleet.events``——Sanji 建活動／開會議的執行端。

另有 ``minutes_dm``：私訊 Sanji 的話先問 ``fleet.minutes.tracker``，是不是會議紀錄審核
（曉宣核准／要改）或行動項目（「1 做完了」）的事（2026-11-17，見 freedom-fleet
``docs/runbooks/minutes-review.md``）。

ADR-071 分工 B（2026-11-16 修修裁決）：nakama 只管 Slack 對話；開 Zoom、做封面、發待審核
活動都在 freedom-fleet repo 執行。兩個 repo 在 VPS 上是同一台機器、同一個執行身分（root），
所以用子行程呼叫，stdin/stdout 交換 JSON（契約見 freedom-fleet ``fleet/events/cli.py``）。

設定（env）：
- ``FREEDOM_FLEET_DIR``：freedom-fleet repo 根目錄（預設 ``/home/freedom-fleet``）
- ``FREEDOM_FLEET_PYTHON``：該 repo 的 Python（預設 ``<dir>/.venv/bin/python``）

子行程只拿到最小環境變數：金鑰一律讀 freedom-fleet 自己的 ``.env``，nakama 的金鑰不外流、
也不會蓋掉對方的設定。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from shared.log import get_logger

logger = get_logger("nakama.fleet_events")

DEFAULT_DIR = "/home/freedom-fleet"

# 各指令的逾時：封面可能要等 AI 插畫（freedom-fleet 端 180s）
TIMEOUTS_S = {"zoom": 60.0, "cover": 240.0, "post": 120.0}

# 會造成「重複動作」的指令：逾時時對方可能已經做完
_SIDE_EFFECT_COMMANDS = {"zoom", "post"}

_PASS_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE")


class FleetEventsError(RuntimeError):
    """freedom-fleet 指令失敗。``maybe_done`` = 對方可能已經做完（不可盲目重試）。"""

    def __init__(self, message: str, *, maybe_done: bool = False):
        super().__init__(message)
        self.maybe_done = maybe_done


def _paths() -> tuple[Path, str]:
    root = Path(os.environ.get("FREEDOM_FLEET_DIR", "").strip() or DEFAULT_DIR)
    python = os.environ.get("FREEDOM_FLEET_PYTHON", "").strip() or str(
        root / ".venv" / "bin" / "python"
    )
    return root, python


def _child_env() -> dict[str, str]:
    env = {k: v for k in _PASS_ENV if (v := os.environ.get(k))}
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run(command: str, payload: dict[str, Any]) -> dict[str, Any]:
    """執行 ``python -m fleet.events <command>``，回傳成功的輸出 JSON。失敗丟 FleetEventsError。"""
    if command not in TIMEOUTS_S:
        raise ValueError(f"unknown fleet.events command: {command}")
    return _call(
        "fleet.events",
        command,
        payload,
        timeout=TIMEOUTS_S[command],
        side_effect=command in _SIDE_EFFECT_COMMANDS,
    )


def _call(
    module: str, command: str, payload: dict[str, Any], *, timeout: float, side_effect: bool
) -> dict[str, Any]:
    root, python = _paths()
    if not root.is_dir():
        raise FleetEventsError(f"找不到 freedom-fleet 目錄：{root}（設定 FREEDOM_FLEET_DIR）")

    try:
        proc = subprocess.run(
            [python, "-m", module, command],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(root),
            env=_child_env(),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise FleetEventsError(
            f"freedom-fleet {command} 逾時（{timeout:.0f}s）",
            maybe_done=side_effect,
        ) from exc
    except OSError as exc:  # 找不到 python 執行檔等：一定沒執行
        raise FleetEventsError(f"無法啟動 freedom-fleet：{exc}") from exc

    stdout = (proc.stdout or "").strip()
    try:
        out = json.loads(stdout) if stdout else None
    except json.JSONDecodeError:
        out = None
    if not isinstance(out, dict):
        tail = (proc.stderr or "").strip()[-300:]
        logger.error(f"{module} {command} bad output rc={proc.returncode} stderr={tail!r}")
        raise FleetEventsError(
            f"freedom-fleet {command} 沒回傳結果（exit {proc.returncode}）",
            maybe_done=side_effect,
        )
    if not out.get("ok"):
        raise FleetEventsError(
            str(out.get("error") or "unknown error"),
            maybe_done=bool(out.get("maybe_created")),
        )
    return out


MINUTES_DM_TIMEOUT_S = 20.0


def minutes_dm(user_id: str, text: str, *, in_flow: bool) -> str | None:
    """私訊 Sanji 的話是不是會議紀錄審核／行動項目的事（``fleet.minutes.tracker dm``）。

    是 → 回 Sanji 要回的話；不是、或 freedom-fleet 出錯 → None（照原本流程處理，不擋聊天）。
    """
    return _minutes_check({"user": user_id, "text": text, "in_flow": in_flow, "where": "dm"})


def minutes_mention(user_id: str, text: str) -> str | None:
    """頻道裡 @Sanji 的話是不是行動項目「做完了」「待辦」（2026-11-17 修修裁決：提醒改發頻道）。"""
    return _minutes_check({"user": user_id, "text": text, "where": "channel"})


def _minutes_check(payload: dict) -> str | None:
    try:
        out = _call(
            "fleet.minutes.tracker",
            "dm",
            payload,
            timeout=MINUTES_DM_TIMEOUT_S,
            side_effect=True,
        )
    except FleetEventsError as exc:
        logger.error(f"minutes dm check failed: {exc}")
        return None
    if not out.get("handled"):
        return None
    return str(out.get("reply") or "") or None
