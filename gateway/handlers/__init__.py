"""Handler registry — 自動載入所有 agent handlers。"""

from __future__ import annotations

from gateway.handlers.base import BaseHandler
from gateway.handlers.nami import NamiHandler
from gateway.handlers.orchestrator import OrchestratorHandler
from gateway.handlers.sanji import SanjiHandler
from gateway.handlers.zoro import ZoroHandler

_HANDLERS: dict[str, BaseHandler] = {
    "nami": NamiHandler(),
    "sanji": SanjiHandler(),
    "zoro": ZoroHandler(),
    "orchestrator": OrchestratorHandler(),
}


def get_handler(agent: str) -> BaseHandler | None:
    """取得 agent 對應的 handler，不存在則回傳 None。"""
    return _HANDLERS.get(agent)


def list_agents() -> list[str]:
    """列出所有已註冊的 agent 名稱。"""
    return list(_HANDLERS.keys())
