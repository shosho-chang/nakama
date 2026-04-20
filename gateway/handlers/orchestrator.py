"""OrchestratorHandler — 把 brainstorm orchestrator 包成 BaseHandler。

純 adapter：解析 topic 後呼叫 `gateway.orchestrator.run_brainstorm()`，再把
`BrainstormResult` 轉 Slack blocks 塞進 HandlerResponse。沒有 state、沒有
continuation — 一次性指令。
"""

from __future__ import annotations

import re

from gateway.handlers.base import BaseHandler, HandlerResponse
from gateway.orchestrator import format_brainstorm_blocks, run_brainstorm
from shared.log import get_logger

logger = get_logger("nakama.gateway.orchestrator.handler")

# 允許的觸發前綴（英中都收）
_TRIGGER_PATTERN = re.compile(
    r"^\s*(brainstorm|腦力激盪|討論一下|一起討論|來討論)\s*[:：]?\s*",
    re.IGNORECASE,
)


def _extract_topic(text: str) -> str:
    """從 user 文字中把 brainstorm 前綴剝掉，剩下的就是 topic。"""
    stripped = _TRIGGER_PATTERN.sub("", text, count=1).strip()
    return stripped


class OrchestratorHandler(BaseHandler):
    """brainstorm orchestrator 的 Slack adapter。"""

    agent_name = "orchestrator"
    supported_intents = ["brainstorm", "general"]

    def handle(self, intent: str, text: str, user_id: str) -> HandlerResponse:
        topic = _extract_topic(text)
        logger.info(f"orchestrator: user={user_id} topic={topic!r}")
        result = run_brainstorm(topic)
        fallback, blocks = format_brainstorm_blocks(result)
        return HandlerResponse(text=fallback, blocks=blocks)
