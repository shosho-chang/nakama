"""BaseHandler — 所有 agent Slack handler 的抽象基底。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class HandlerResponse:
    """Handler 回傳的回應。"""

    text: str
    blocks: list | None = None
    thread_ts: str | None = None
    emit_event: dict | None = None


class BaseHandler(ABC):
    """Agent handler 基底類別。"""

    agent_name: str = "base"
    supported_intents: list[str] = []

    def can_handle(self, intent: str) -> bool:
        """此 handler 是否支援指定 intent。"""
        return intent in self.supported_intents or intent == "general"

    @abstractmethod
    def handle(self, intent: str, text: str, user_id: str) -> HandlerResponse:
        """執行 agent 邏輯並回傳回應。"""
        ...

    def suggest_redirect(self, intent: str) -> str | None:
        """此 agent 無法處理時，建議轉介給誰。"""
        from gateway.router import INTENT_TO_AGENT

        correct = INTENT_TO_AGENT.get(intent)
        if correct and correct != self.agent_name:
            return correct
        return None
