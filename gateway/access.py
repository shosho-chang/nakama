"""Slack gateway 使用者白名單。

Slack workspace 一旦有其他成員，任何人都能 DM / @mention 已安裝的 agent bot；
Nami 背後是修修本人的 Gmail、行事曆、vault，不能對整個 workspace 開放。

白名單 = `SLACK_ALLOWED_USER_IDS`（逗號或空白分隔）∪ `SLACK_USER_ID_SHOSHO`。
兩個都沒設 → `gateway.bot.run()` 拒絕啟動（fail-closed），不會因為漏設 env
就默默對所有人開放。

每次呼叫都重讀 env：改白名單只要改 `.env` 並重啟 gateway，不必改 code。
"""

from __future__ import annotations

import os
import re

_SPLIT = re.compile(r"[\s,]+")


def allowed_user_ids() -> frozenset[str]:
    """目前允許使用 agent 的 Slack user ID 集合。"""
    raw = os.environ.get("SLACK_ALLOWED_USER_IDS", "")
    ids = {part for part in _SPLIT.split(raw) if part}
    owner = os.environ.get("SLACK_USER_ID_SHOSHO", "").strip()
    if owner:
        ids.add(owner)
    return frozenset(ids)


def is_allowed(user_id: str) -> bool:
    """`user_id` 是否在白名單內。空字串一律不允許。"""
    return bool(user_id) and user_id in allowed_user_ids()
