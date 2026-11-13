"""誰可以在 Slack 上叫哪個 agent。

規則：

- 修修（env ``SLACK_USER_ID_SHOSHO``）可以叫所有 agent。
- 其他人預設一律不行；要開放，就把對方的 Slack user ID 列進 ``config.yaml`` 的
  ``gateway.slack.extra_allowed_users.<agent>``。
- ``SLACK_USER_ID_SHOSHO`` 沒設時不會因此放行任何人（fail closed），只剩白名單上的人能用。

Franky 的 Slack bot 只發告警 DM、不接收訊息，所以不經過這裡；``/franky`` 斜線指令
（掛在 Nami app 上）會經過，一樣只有修修能用。
"""

from __future__ import annotations

import os

from shared.config import load_config

OWNER_ENV = "SLACK_USER_ID_SHOSHO"

DENY_MESSAGE = "抱歉，我目前只接受修修的指令。"


def _owner_id() -> str:
    return os.environ.get(OWNER_ENV, "").strip()


def extra_allowed_users(agent: str) -> set[str]:
    """``config.yaml`` 裡額外開放給 ``agent`` 的 Slack user ID。"""
    cfg = load_config()
    table = cfg.get("gateway", {}).get("slack", {}).get("extra_allowed_users") or {}
    users = table.get(agent) or []
    return {str(u).strip() for u in users if str(u).strip()}


def is_allowed(agent: str, user_id: str) -> bool:
    """``user_id`` 可不可以叫 ``agent``。"""
    user_id = (user_id or "").strip()
    if not user_id:
        return False
    owner = _owner_id()
    if owner and user_id == owner:
        return True
    return user_id in extra_allowed_users(agent)
