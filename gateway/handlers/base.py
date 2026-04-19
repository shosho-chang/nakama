"""BaseHandler — 所有 agent Slack handler 的抽象基底。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Continuation:
    """Handler 要求接續對話時攜帶的資訊。

    bot 會把此次回覆開在 thread 裡，之後在同 thread 的訊息會被路由回
    此 handler 的 `continue_flow(flow_name, state, text, user_id)`，直到
    handler 回傳 `continuation=None` 結束流程，或 30 分鐘無活動自動超時。
    """

    flow_name: str
    state: dict


@dataclass
class HandlerResponse:
    """Handler 回傳的回應。"""

    text: str
    blocks: list | None = None
    thread_ts: str | None = None
    emit_event: dict | None = None
    continuation: Continuation | None = None


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

    def continue_flow(
        self,
        flow_name: str,
        state: dict,
        text: str,
        user_id: str,
    ) -> HandlerResponse:
        """接續由 `handle()` 啟動的多輪流程。預設不支援。

        若 handler 要支援多輪反問，override 此 method：讀取 state + text，
        回傳新的 `HandlerResponse`。要繼續就帶 `continuation=Continuation(...)`，
        要結束就不帶。
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not implement continue_flow (flow_name={flow_name!r})"
        )

    def suggest_redirect(self, intent: str) -> str | None:
        """此 agent 無法處理時，建議轉介給誰。"""
        from gateway.router import INTENT_TO_AGENT

        correct = INTENT_TO_AGENT.get(intent)
        if correct and correct != self.agent_name:
            return correct
        return None
