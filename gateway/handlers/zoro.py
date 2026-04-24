"""Zoro handler — 劍士/偵查 agent 的 Slack 對話回覆。

Slice A（T5.1）：最小可用 handler，載 persona + 走 facade 生回覆。跟 Sanji 結構
對稱。之後（Slice B）會加 brainstorm scout 的主動推題邏輯，handler 這邊不變。

facade 依 `MODEL_ZORO` env 路由 — 未設則 default Sonnet。
"""

from __future__ import annotations

from gateway.handlers.base import BaseHandler, HandlerResponse
from shared import agent_memory
from shared.anthropic_client import set_current_agent
from shared.llm import ask
from shared.log import get_logger
from shared.memory import get_context
from shared.prompt_loader import load_prompt

logger = get_logger("nakama.gateway.zoro")

ZORO_MAX_TOKENS = 1024


class ZoroHandler(BaseHandler):
    """Zoro：情報偵查 handler（第一版無 tool use）。"""

    agent_name = "zoro"
    supported_intents = [
        "general",
        "keyword_research",
        "brainstorm_participant",
    ]

    def handle(self, intent: str, text: str, user_id: str) -> HandlerResponse:
        set_current_agent("zoro")

        try:
            system = load_prompt("zoro", "persona")
        except FileNotFoundError:
            logger.error("zoro persona prompt missing — fallback minimal")
            system = "你是 Zoro，張修修海賊團的劍士，負責情報偵查。用繁體中文，簡短。"

        mem_parts = [
            p for p in [get_context("zoro"), agent_memory.format_as_context("zoro", user_id)] if p
        ]
        if mem_parts:
            system = system + "\n\n" + "\n\n".join(mem_parts)

        try:
            reply = ask(prompt=text, system=system, max_tokens=ZORO_MAX_TOKENS)
        except Exception as e:
            logger.error(f"zoro ask failed: {e}", exc_info=True)
            reply = f"（抱歉，巡邏暫時中斷：{e}。稍後再試。）"

        return HandlerResponse(text=reply)
