"""Sanji handler — 社群廚師角色的對話回覆。

第一版（Q3 P1）：不做 tool use，單純載 persona + 走 facade 生回覆。
facade 依 MODEL_SANJI env 自動把 Sanji 路到 Grok（見 shared/llm_router.py）。

之後（P2/P3）可加社群監控 tool（讀 Fluent Community REST、偵測未回覆）。

多輪流程（執行端在 freedom-fleet，見 ``shared/fleet_events.py``）：
- 建社群活動：含建活動關鍵字（``sanji_event.TRIGGER_KEYWORDS``）→ ``sanji_event``
  （flow_name = ``event_draft``，發「待審核」活動）
- 開內部會議：含開會關鍵字（``sanji_meeting.TRIGGER_KEYWORDS``）→ ``sanji_meeting``
  （flow_name = ``internal_meeting``，只開 Zoom＋Slack 提醒）
其他訊息照舊走聊天。
"""

from __future__ import annotations

from gateway.handlers import sanji_event, sanji_meeting
from gateway.handlers.base import BaseHandler, HandlerResponse
from shared import agent_memory
from shared.llm import ask
from shared.llm_context import set_current_agent
from shared.log import get_logger
from shared.memory import get_context
from shared.prompt_loader import load_prompt

logger = get_logger("nakama.gateway.sanji")

SANJI_MAX_TOKENS = 1024


class SanjiHandler(BaseHandler):
    """Sanji：社群對話 handler（第一版無 tool use）。"""

    agent_name = "sanji"
    supported_intents = ["general", "community_chat", "wellness_qa", "brainstorm_participant"]

    def handle(self, intent: str, text: str, user_id: str) -> HandlerResponse:
        set_current_agent("sanji")

        if sanji_event.is_event_request(text):
            return sanji_event.start_flow(text, user_id)
        if sanji_meeting.is_meeting_request(text):
            return sanji_meeting.start_flow(text, user_id)

        try:
            system = load_prompt("sanji", "persona")
        except FileNotFoundError:
            logger.error("sanji persona prompt missing — fallback minimal")
            system = "你是 Sanji，張修修海賊團的廚師，負責自由艦隊社群。用繁體中文。"

        # 注入 shared 記憶（修修是誰）+ Sanji 的 SQLite 記憶
        mem_parts = [
            p for p in [get_context("sanji"), agent_memory.format_as_context("sanji", user_id)] if p
        ]
        if mem_parts:
            system = system + "\n\n" + "\n\n".join(mem_parts)

        try:
            reply = ask(
                prompt=text, system=system, max_tokens=SANJI_MAX_TOKENS, call_class="interactive"
            )
        except Exception as e:
            logger.error(f"sanji ask failed: {e}", exc_info=True)
            reply = f"（抱歉，廚房臨時出狀況：{e}。稍後再試一次。）"

        return HandlerResponse(text=reply)

    def continue_flow(
        self,
        flow_name: str,
        state: dict,
        text: str,
        user_id: str,
    ) -> HandlerResponse:
        """接續建活動／開會議流程；其他 flow 不支援（bot 會結束該 conversation）。"""
        if flow_name == sanji_event.FLOW_NAME:
            set_current_agent("sanji")
            return sanji_event.continue_flow(state, text, user_id)
        if flow_name == sanji_meeting.FLOW_NAME:
            set_current_agent("sanji")
            return sanji_meeting.continue_flow(state, text, user_id)
        return super().continue_flow(flow_name, state, text, user_id)
