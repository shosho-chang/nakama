"""Sanji handler — 社群廚師角色的對話回覆。

第一版（Q3 P1）：不做 tool use，單純載 persona + 走 facade 生回覆。
facade 依 MODEL_SANJI env 自動把 Sanji 路到 Grok（見 shared/llm_router.py）。

之後（P2/P3）可加社群監控 tool（讀 Fluent Community REST、偵測未回覆）。
"""

from __future__ import annotations

from gateway.handlers.base import BaseHandler, HandlerResponse
from shared.anthropic_client import set_current_agent
from shared.llm import ask
from shared.log import get_logger
from shared.prompt_loader import load_prompt

logger = get_logger("nakama.gateway.sanji")

SANJI_MAX_TOKENS = 1024


class SanjiHandler(BaseHandler):
    """Sanji：社群對話 handler（第一版無 tool use）。"""

    agent_name = "sanji"
    supported_intents = ["general", "community_chat", "wellness_qa", "brainstorm_participant"]

    def handle(self, intent: str, text: str, user_id: str) -> HandlerResponse:
        # set_current_agent 讓 router / cost tracking 吃到「sanji」
        # → MODEL_SANJI env 自動走 Grok，$$ 落 api_calls 的 agent 欄位也對
        set_current_agent("sanji")

        try:
            system = load_prompt("sanji", "persona")
        except FileNotFoundError:
            logger.error("sanji persona prompt missing — fallback minimal")
            system = "你是 Sanji，張修修海賊團的廚師，負責自由艦隊社群。用繁體中文。"

        try:
            reply = ask(prompt=text, system=system, max_tokens=SANJI_MAX_TOKENS)
        except Exception as e:
            logger.error(f"sanji ask failed: {e}", exc_info=True)
            reply = f"（抱歉，廚房臨時出狀況：{e}。稍後再試一次。）"

        return HandlerResponse(text=reply)
